import contextlib
import logging
import os
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.crud import app_version_approvals as crud_approvals
from app.crud import apps as crud_apps
from app.database import get_db
from app.models import User, UserRole
from app.schemas import (
    AppCreate,
    AppResponse,
    AppUpdate,
    AppVersionApprovalResponse,
    AppVersionApprovalSubmit,
    AppWithVersions,
)
from app.services.git_service import git_service
from app.utils.app_image import build_image_data_url, parse_image_data_url
from app.utils.keycloak_auth import get_current_user_keycloak
from app.utils.permissions import ensure_resource_access


def _serialize_app(app):
    """Replace ``app.image`` (bytes) with the data-URL form in-place.

    The ORM model carries the raw bytes plus a separate mime column.
    The Pydantic ``AppResponse`` schema declares ``image: Optional[str]``
    and uses ``from_attributes=True``, so Pydantic reads ``app.image``
    directly. Overwriting that attribute with the rebuilt data-URL
    means the response serialiser sees a string and the wire format
    matches the schema. Returns ``app`` so callers can chain.
    """
    if app is None:
        return None
    raw_bytes = getattr(app, "image", None)
    if isinstance(raw_bytes, (bytes, memoryview, bytearray)):
        app.image = build_image_data_url(bytes(raw_bytes), getattr(app, "image_mime", None))
    return app

router = APIRouter()


# ----------------------------------------------------------------
# Pydantic schema for the /apps/{id}/variables response
# ----------------------------------------------------------------
# Bug #10 — der Endpoint lieferte vorher ``list[dict[str, Any]]`` und
# damit kein OpenAPI-Schema. Wir spiegeln hier die genauen Keys, die
# ``_parse_one_variable`` heute liefert (gemischt snake/camelCase),
# damit das Frontend ohne Anpassung weiterläuft und das generierte
# OpenAPI-Dokument den Wizard-Vertrag dokumentiert.
class _MarkerErrorPayload(BaseModel):
    variable: str
    message: str
    location: str
    # ``code`` ist neu (siehe MarkerError-Schema oben); existierende
    # Clients ignorieren das Feld einfach.
    code: str | None = None


class AppVariableResponse(BaseModel):
    """Shape of one entry in ``GET /apps/{id}/variables``.

    Keys match what ``_parse_one_variable`` writes to the dict
    EXACTLY — the frontend (NewDeploymentVariableView.vue) reads
    ``osType``/``osMode``/``osMulti``/``osScope``/``varScope``/
    ``fileExtensions`` in camelCase and the rest in snake/lowercase.
    Don't auto-aliaserate here; we keep the keys verbatim.
    """

    # ``populate_by_name`` lets callers construct with either field
    # name or alias; we keep the dict-style names as the canonical
    # source (mostly because the existing code path builds a dict
    # and we use ``.model_validate`` over it).
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str
    type: str
    description: str | None = None
    # Bug #7 — Default ist jetzt typisiert (Number/Bool/List/Dict/None),
    # nicht mehr nur String. ``Any`` ist hier bewusst breit, weil HCL
    # eine ganze Literal-Familie abdeckt.
    default: Any | None = None
    required: bool
    source: str
    osType: str | None = None
    osMode: str | None = None
    osMulti: bool | None = None
    osScope: str | None = None
    varScope: str | None = None
    fileExtensions: list[str] | None = None
    markerError: _MarkerErrorPayload | None = None
    # ``template_key`` is null for ``source = terraform`` variables and
    # carries the per-template key (``default`` for the legacy layout,
    # or the subdirectory name like ``webserver``/``database`` in
    # multi-image apps) for ``source = packer`` variables. Lets the
    # wizard group Packer variables per image and avoid name collisions
    # across templates.
    template_key: str | None = None


# ----------------------------------------------------------------
# OPENSTACK-MARKER PARSING (HCL VARIABLES)
# ----------------------------------------------------------------
# Apps deklarieren Value-Help für OpenStack-Resourcen ausschließlich
# über einen expliziten Marker in der ``description`` der Variable.
# KEINE Heuristik. Keine Namens-Inferenz. Keine Description-Substring-
# Matches. Wer den Marker nicht setzt, bekommt schlicht einen Free-Text-
# Input — zero magic, voller Kontrolle für den App-Autor.
#
# Grammatik (positional, mit Defaults):
#
#     @openstack:<type>[:<mode>][:<multi>][:<var_scope>]
#
#   <type>   — eine der unten gelisteten Resource-Kinds (siehe ``_OS_TYPES``)
#              ODER LEER. Ein leerer Type-Slot ist erlaubt, wenn der Marker
#              ausschließlich dazu dient, einen ``var_scope`` zu setzen
#              (Beispiel: ``@openstack:::user`` markiert eine sonst freie
#              String-Variable als per-User-scoped, ohne einen Resource-
#              Picker zu erzwingen).
#   <mode>   — 'id' | 'name'   (default: 'name'; siehe auch
#              ``_NAME_ONLY_TYPES`` für Resourcen, bei denen 'id' praktisch
#              sinnlos ist)
#   <multi>  — 'multi' | 'list' | 'single'  ('list' ist Synonym für 'multi'.
#              Default ohne Marker-Slot: aus dem HCL-Typ abgeleitet —
#              ``list(...)``/``set(...)``/``tuple(...)``/``list``/``set``
#              → multi, sonst single. ``map(...)`` und ``object(...)`` sind
#              technische Kollektionen, gelten hier aber als single — wer
#              die als Multi will, schreibt ``:multi`` explizit.)
#   <var_scope> — 'all' | 'team' | 'user'   (default: 'all'). Steuert, ob
#              der Wizard genau EIN Eingabefeld rendert (``all``), eines
#              pro Team (``team``) oder eines pro User (``user``). Bei
#              ``team``/``user`` muss der HCL-Type eine ``map(...)`` sein,
#              weil das Backend die Slot-Map 1:1 in die Terraform-
#              Variable schreibt. Für Packer-Variablen ist nur ``all``
#              erlaubt — ein Image wird einmal gebaut und kann nicht
#              per-Team divergieren.
#
# Beispiele:
#     @openstack:network                        → network, name-mode, multi aus HCL
#     @openstack:network:id                     → network, id-mode
#     @openstack:security_group:name:multi      → SG, name-mode, multi
#     @openstack:flavor::multi                  → leerer Mode-Slot ⇒ default 'name'
#     @openstack:image:id:single                → image, id-mode, single (auch wenn
#                                                  HCL ``list(string)`` wäre →
#                                                  Konfliktcheck schlägt zu)
#     @openstack:flavor:id:single:team          → pro Team eine Flavor-ID; HCL muss
#                                                  ``map(string)`` sein.
#     @openstack:::user                         → reine Free-Text-Variable, pro User
#                                                  scoped; HCL muss ``map(...)`` sein.
#
# Der Marker darf an einer beliebigen Stelle in der Description stehen,
# muss aber an einem Wort-Ende terminieren (Whitespace, Zeilenende oder
# Satzzeichen `.,;:!?)]"'`).
#
# Mehrere Marker in einer Description: der erste mit BEKANNTEM Type
# gewinnt. Marker mit unbekanntem Type (z.B. „migration:
# ``@openstack:vm`` → ``@openstack:network``" als Doku-Snippet) werden
# übersprungen, damit der echte Marker dahinter trotzdem zieht. Wenn
# KEIN Marker einen bekannten Type hat, ist das ein Fehler (der erste
# unbekannte wird mit ``meintest du …?``-Hint gemeldet).
#
# Fehlerbehandlung: malformierte ODER ungültige Marker werfen eine
# ``MarkerError``. Diese wird beim Parser pro Variable gefangen und im
# Variable-Payload als ``markerError``-Feld an das Frontend mitgesendet
# — die betroffene Variable rendert dann als Free-Text mit Inline-Hint,
# alle anderen Variablen bleiben benutzbar. Dadurch ist EIN Tippfehler
# kein Wizard-Showstopper, der App-Autor sieht ihn aber direkt im UI.
# ----------------------------------------------------------------

# Liste der unterstützten OpenStack-Resource-Types. Muss konsistent
# sein mit:
#  - backend/app/routers/openstack_resources.py (Listen-Endpoints)
#  - frontend/src/types/index.ts (`AppVariableOsType`)
#  - frontend/src/components/OpenStackResourcePicker.vue (Render)
_OS_TYPES: set[str] = {
    "network",
    "subnet",
    "flavor",
    "image",
    "keypair",
    "security_group",
    "floating_ip_pool",
    "volume",
    "router",
    "availability_zone",
    # ``file`` is a special pseudo-resource: it doesn't pick from a
    # remote OpenStack API, it tells the wizard to render a file-upload
    # widget and route the bytes into ``userInputVar.terraform`` so the
    # template can drop them onto the VM via cloud-init ``write_files``.
    # The mode slot carries the scope (``all``/``team``/``user``); the
    # multi slot carries the PFLICHT-Endungsfilter (z.B. ``pdf`` oder
    # ``pdf|docx``) — ein File-Marker ohne Filter wird abgelehnt.
    "file",
}

# Allowed scope tokens for ``@openstack:file:<scope>``. Reuses the
# mode slot of the marker grammar — keeps the regex shape unchanged
# while teaching the parser to interpret the slot per-type.
_FILE_SCOPES: set[str] = {"all", "team", "user"}

# Pflicht-Filter im vierten Marker-Slot bei File-Variablen. Wir
# erlauben nur Buchstaben+Ziffern und ``|`` als Trenner; case-
# insensitive matchen, intern lowercased. Beispiele: ``pdf``,
# ``pdf|docx|txt``. Kein Leerwert — ein File-Marker ohne Filter
# ist Fehler.
_FILE_EXTENSIONS_RE = re.compile(r"^[a-z0-9]+(?:\|[a-z0-9]+)*$")

# Erlaubte Werte für den allgemeinen ``var_scope``-Slot (vierter
# Slot bei non-file-Markern). ``all`` ist der Default — Variablen
# ohne Marker sowie Marker ohne 4. Slot werden auf ``all`` aufgelöst.
_VAR_SCOPES: set[str] = {"all", "team", "user"}

# Resource-Kinds, die in OpenStack faktisch keine UUID haben oder
# durchgängig namensbasiert adressiert werden — z.B. Keypairs (Nova
# nutzt nur Namen), Availability Zones (haben gar keine UUID),
# Floating-IP-Pools (External Networks, in Modulen via Name).
#
# Marker-only-Modus: dieser Default greift NUR, wenn der Autor mode
# weglässt. ``@openstack:keypair`` → mode='name'. ``@openstack:keypair:id``
# wird respektiert (App-Autor weiß was er tut), aber praktisch sinnlos.
# Wir warnen nicht aktiv — wer einen Picker für UUID-lose Resourcen will,
# bekommt eben eine leere ID-Liste und merkt es spätestens beim Deploy.
_NAME_ONLY_TYPES: set[str] = {"keypair", "availability_zone", "floating_ip_pool"}

# Marker-Regex. Wir matchen das ganze Token an Wort-Grenzen, damit
# Beispiele in Prosa wie ``"siehe @openstack:network in der Doku"``
# erkannt werden, aber ein zufälliges ``"@openstackbar"`` NICHT matcht.
# Slot-Inhalt darf KEIN Whitespace haben. Fünf oder mehr Doppelpunkte
# = malformed (siehe ``_TOO_MANY_SEGMENTS_RE``).
#
# Boundary-Zeichen rechts: alles was kein Identifier-Zeichen ist —
# Whitespace, Zeilenende, gängige Satzzeichen ``. , ; : ! ? ) ] " '``.
# Linke Grenze: Anfang oder dieselben Boundary-Zeichen.
#
# Slot-Inhalte:
#  * Slot 1 (type): ``[A-Za-z][A-Za-z0-9_]*`` oder LEER. Ein leerer
#    Type-Slot ist semantisch „nur var_scope setzen, kein Resource-
#    Picker erzwingen".
#  * Slots 2/3: ``[A-Za-z]*`` (heutiges Verhalten).
#  * Slot 4 (var_scope für non-file, file-extensions-Filter für file):
#    ``[A-Za-z0-9|]*``. Das ``|`` ist nur für den File-Filter-Fall
#    nötig (z.B. ``pdf|docx``); für var_scope-Werte wäre es überflüssig,
#    schadet aber nicht. Die semantische Trennung passiert im Parser.
_MARKER_RE = re.compile(
    r"""
    (?:^|(?<=[\s.,;:!?()\[\]"']))   # Linke Grenze: Anfang oder Whitespace/Satzzeichen
    @openstack
    :([A-Za-z][A-Za-z0-9_]*)?       # 1: type (kann leer sein → nur-scope-Marker)
    (?::([A-Za-z]*))?               # 2: mode-Slot (kann leer sein)
    (?::([A-Za-z]*))?               # 3: multi-Slot (kann leer sein)
    (?::([A-Za-z0-9|]*))?           # 4: var_scope / file-extensions (kann leer sein)
    (?=$|[\s.,;:!?)\]"'])           # Rechte Grenze
    """,
    # IGNORECASE: Bug #13 — Marker-Prefix soll case-insensitive akzeptiert
    # werden (``@OpenStack:flavor`` ist die gleiche Intent wie
    # ``@openstack:flavor``). Die nachgelagerte Lowercasing-Pipeline in
    # ``_parse_marker`` normalisiert die Slot-Inhalte unverändert.
    re.VERBOSE | re.IGNORECASE,
)

# Bug #12 — Whitespace zwischen Marker-Segmenten silently truncate:
# Erkennt einen direkt nach einem Match weiterführenden
# „<whitespace>:<token>"-Fortsetzungsversuch (z.B.
# ``@openstack:flavor :id``). ``_MARKER_RE`` stoppt am Whitespace und
# würde den Rest ignorieren — wir feuern hier einen expliziten Fehler,
# damit der Tippfehler sichtbar wird.
_MARKER_WHITESPACE_CONT_RE = re.compile(r"\s+:\s*[A-Za-z]")

# Komma als Slot-Trenner ist ein häufiger Tippfehler — siehe Erläuterung
# an der Call-Site. Match Form: ``<tail starts with>,<token-char>``.
_MARKER_COMMA_CONT_RE = re.compile(r",\s*[A-Za-z0-9|]")

# Schnell-Check: der Marker hat zu viele Segmente?
# ``@openstack:network:id:multi:team:extra`` → fail.
# Wir verlangen, dass JEDES der 5+ Segmente nicht-leer ist, sonst
# würde ``@openstack:network:id:multi:team:`` (Trailing-Colon, klare
# 4-Slot-Form) fälschlich als „zu viele Segmente" gefangen.
_TOO_MANY_SEGMENTS_RE = re.compile(
    r"@openstack(?::[A-Za-z0-9_|]+){5,}",
    re.IGNORECASE,
)


# Erkennung eines „Marker-versuchten-aber-falsch"-Inputs. Wir feuern,
# wenn die Description ``@openstack:`` enthält, der strikte Marker-Regex
# aber NICHTS findet. Typische Fälle: Bindestrich/Slash/Equals als
# Trenner, Whitespace im Marker, leerer Type, kaputte Typen, ...
# Wir matchen `@openstack` gefolgt von `:` ODER von Whitespace+`:`,
# damit „@openstack: <type>" auch greift.
_BAD_PREFIX_RE = re.compile(
    r"@openstack\s*:",
    re.IGNORECASE,
)


class MarkerError(ValueError):
    """
    Erhoben, wenn ein ``@openstack``-Marker syntaktisch oder semantisch
    fehlerhaft ist. Wird im Endpoint in HTTP 400 übersetzt, damit der
    App-Autor den Fehler sofort beim ersten ``GET /apps/{id}/variables``
    sieht — statt dass die Variable stillschweigend als Free-Text-Input
    erscheint.

    ``code`` ist ein stabiler, maschinen-lesbarer Fehler-Schlüssel
    (z.B. ``MARKER_WHITESPACE``). Die ``message`` ist heute deutsch und
    bleibt menschen-lesbar — der ``code`` macht künftige i18n möglich,
    ohne dass das Frontend auf den genauen Text matchen müsste.
    """

    def __init__(self, var_name: str, message: str, code: str = "MARKER_INVALID"):
        super().__init__(f"Variable '{var_name}': {message}")
        self.var_name = var_name
        self.message = message
        self.code = code


def _parse_marker(
    var_name: str, var_type: str, description: str, source: str = "terraform"
) -> tuple[str | None, str | None, bool | None, str | None, str | None, list[str] | None]:
    """
    Parst den ``@openstack:<type>[:<mode>][:<multi>][:<var_scope>]``-Marker
    aus der Description. Liefert ``(None, None, None, None, None, None)``
    wenn KEIN Marker da ist (das ist KEIN Fehler — die Variable wird dann
    als Free-Text gerendert).

    Multi-Marker-Verhalten: Findet die Funktion mehrere Marker, nimmt sie
    den ersten, dessen Type bekannt ist ODER der einen leeren Type-Slot
    hat (= reiner var_scope-Marker). Das ist absichtlich tolerant —
    Apps zitieren manchmal ältere Marker-Schreibweisen in der Description
    („migration: ``@openstack:vm`` → ``@openstack:network``"). Mode/Multi-
    Validierungs-Fehler des gewählten Markers sind weiterhin hart, weil
    sie konkret und nicht-tolerierbar sind.

    Wirft ``MarkerError`` bei:
      - malformiertem Marker (zu viele Segmente, internes Whitespace,
        unbekannte mode/multi/scope-Tokens, Slot-Trenner mit Sonderzeichen
        statt ``:``)
      - widersprüchlichem Marker vs. HCL-Type (``:single`` mit
        ``type = list(...)`` oder ``:multi`` mit ``type = number``;
        ``:team``/``:user`` mit ``type = string``)
      - file-spezifisch: ungültigem Scope (``@openstack:file:foo``),
        fehlendem Endungs-Filter (``@openstack:file:all``) oder
        ungültigem Filter (``@openstack:file:all:pdf,docx``).
      - packer-source mit ``var_scope ∈ {team, user}``.

    Returns: ``(os_type, mode, multi, file_scope, var_scope, file_exts)``.

    * ``os_type``     — None, wenn der Marker leer-type war (= reiner
                        var_scope-Marker).
    * ``mode``        — nur für non-file gesetzt.
    * ``multi``       — nur für non-file gesetzt.
    * ``file_scope``  — nur für file gesetzt (``all``/``team``/``user``).
    * ``var_scope``   — generischer Scope (``all``/``team``/``user``).
                        Bei file-Variablen spiegelt das den ``file_scope``,
                        damit der Wizard EINE einzige Quelle für Slot-
                        Auflösung hat.
    * ``file_exts``   — nur für file gesetzt: Liste erlaubter Endungen,
                        z.B. ``["pdf", "docx"]``. Reihenfolge stabil
                        gemäß Marker-Reihenfolge.
    """
    if not description:
        return (None, None, None, None, None, None)

    # Sechs+ Segmente (also fünf+ Doppelpunkte nach ``@openstack:``) sind
    # nie legitim. Schnellt zuerst durch, BEVOR der Haupt-Regex (der
    # nach 4 Slots aufhört) das gar nicht mitkriegt.
    if _TOO_MANY_SEGMENTS_RE.search(description):
        raise MarkerError(
            var_name,
            "marker hat zu viele Segmente — erlaubt: "
            "@openstack:<type>[:<mode>][:<multi>][:<var_scope>]",
            code="MARKER_TOO_MANY_SEGMENTS",
        )

    matches = list(_MARKER_RE.finditer(description))
    if not matches:
        # Strikter Hard-Fail-Pfad: jemand hat ``@openstack:`` getippt,
        # aber unsere Grammatik passt nicht — z.B. wegen Whitespace,
        # Bindestrich, ``=``, Slash. Stilles Ignorieren würde den Bug
        # verstecken (Variable rendert als Free-Text und niemand merkt
        # was). Lieber laut.
        if _BAD_PREFIX_RE.search(description):
            raise MarkerError(
                var_name,
                "marker konnte nicht geparst werden — erlaubt ist nur "
                "``@openstack:<type>[:<mode>][:<multi>][:<var_scope>]`` mit "
                "Doppelpunkten als Trenner und ohne Whitespace zwischen "
                "den Segmenten",
                code="MARKER_UNPARSEABLE",
            )
        return (None, None, None, None, None, None)

    # Bug #12 — Whitespace zwischen Marker-Segmenten silently truncate:
    # ``_MARKER_RE`` stoppt am ersten Whitespace, also wird
    # ``@openstack:flavor :id`` nur als ``@openstack:flavor`` geparst —
    # der ``:id``-Slot fällt unbemerkt unter den Tisch. Wir prüfen für
    # JEDEN Match, ob unmittelbar nach dem Span ein
    # ``<whitespace>:<token>``-Fortsetzungsversuch steht und werfen
    # einen klaren Fehler, statt den Tippfehler zu schlucken.
    for m in matches:
        tail = description[m.end():]
        if _MARKER_WHITESPACE_CONT_RE.match(tail):
            raise MarkerError(
                var_name,
                "marker enthält Whitespace zwischen den Segmenten — "
                "schreibe ihn ohne Leerzeichen (z.B. "
                "``@openstack:flavor:id:multi`` statt "
                "``@openstack:flavor :id :multi``)",
                code="MARKER_WHITESPACE",
            )

    # Komma als Slot-Trenner ist ein häufiger Tippfehler — der einzig
    # erlaubte Trenner ist ``|`` (z.B. ``@openstack:file:all:pdf|docx``).
    # ``_MARKER_RE`` matcht nur bis zum Komma, also bliebe ``,docx`` als
    # stiller Rest-Müll in der Description. Wir erkennen ``<match>,<token>``
    # explizit und werfen einen klaren Fehler — der App-Autor sieht die
    # echte Ursache statt eines kryptischen „extension fehlt"-Fehlers.
    for m in matches:
        tail = description[m.end():]
        if _MARKER_COMMA_CONT_RE.match(tail):
            raise MarkerError(
                var_name,
                "ungültiger Endungsfilter mit Komma — marker-Slots werden "
                "mit ``|`` getrennt, nicht mit Komma (z.B. "
                "``@openstack:file:all:pdf|docx`` statt "
                "``@openstack:file:all:pdf,docx``)",
                code="MARKER_FILE_INVALID_EXTENSIONS",
            )

    # Erster Marker mit BEKANNTEM Type ODER mit leerem Type-Slot
    # (= nur-var_scope-Marker) gewinnt. Marker mit unbekanntem,
    # nicht-leerem Type werden übersprungen (toleriert).
    first_unknown: tuple[str, str] | None = None  # (raw_type, suggestion)
    chosen = None
    for m in matches:
        raw_type = (m.group(1) or "")
        os_type_candidate = raw_type.lower()
        if raw_type == "" or os_type_candidate in _OS_TYPES:
            chosen = (m, raw_type, m.group(2), m.group(3), m.group(4))
            break
        if first_unknown is None:
            first_unknown = (raw_type, _closest_match(os_type_candidate, _OS_TYPES) or "")

    if chosen is None:
        # Es gab Marker, aber alle mit unbekannten Types. Hartes Fail
        # mit Hint auf den ersten — das ist mit hoher Wahrscheinlichkeit
        # der Tippfehler des Autors.
        raw_type, suggestion = first_unknown  # type: ignore[misc]
        hint = f"; meintest du '{suggestion}'?" if suggestion else ""
        raise MarkerError(
            var_name,
            f"unbekannter resource-type '{raw_type}'{hint} — "
            f"erwartet: {sorted(_OS_TYPES)}",
            code="MARKER_UNKNOWN_OS_TYPE",
        )

    _, raw_type, raw_mode, raw_multi, raw_scope = chosen
    os_type: str | None = raw_type.lower() if raw_type else None

    # Hilfs-Parser für den vierten Slot bei non-file-Markern. Wir
    # ziehen den hoch, damit der File-Zweig später unabhängig davon
    # entscheiden kann, ob er den Slot als Extensions oder als
    # var_scope interpretiert.
    def _parse_var_scope(slot: str | None) -> str | None:
        if slot is None or slot == "":
            return None
        rs = slot.lower()
        if rs in _VAR_SCOPES:
            return rs
        suggestion = _closest_match(rs, _VAR_SCOPES)
        hint = f"; meintest du '{suggestion}'?" if suggestion else ""
        raise MarkerError(
            var_name,
            f"ungültiger var_scope '{slot}'{hint} — erwartet "
            f"{sorted(_VAR_SCOPES)}",
            code="MARKER_INVALID_VAR_SCOPE",
        )

    # Nur-var_scope-Marker (``@openstack:::team`` oder Varianten mit
    # weniger Doppelpunkten): kein Type, kein Mode, kein Multi — der
    # Marker hat ausschließlich Scope-Bedeutung. Wenn der App-Autor
    # eine kurze Form schreibt (``@openstack::team`` mit zwei statt
    # vier Slots), landet ``team`` regex-bedingt im Mode-Slot statt im
    # vierten Slot. Wir picken den ersten nicht-leeren Slot von
    # mode/multi/scope und akzeptieren ihn, solange das ein
    # var_scope-Token ist — das macht die Marker-Schreibweise
    # robuster gegen die Anzahl der Doppelpunkte. Mehrere belegte
    # Slots gleichzeitig sind weiterhin Fehler (mehrdeutig).
    if os_type is None:
        candidates = [s for s in (raw_mode, raw_multi, raw_scope) if s not in (None, "")]
        if len(candidates) > 1:
            raise MarkerError(
                var_name,
                "leerer type-slot ist nur in Kombination mit ``var_scope`` "
                "erlaubt (z.B. ``@openstack:::team``); mehrere belegte "
                "Slots sind hier nicht zulässig",
                code="MARKER_EMPTY_TYPE_AMBIGUOUS",
            )
        var_scope = _parse_var_scope(candidates[0] if candidates else None)
        if var_scope is None:
            raise MarkerError(
                var_name,
                "leerer Marker — entweder einen resource-type angeben "
                "(z.B. ``@openstack:flavor``) oder einen var_scope "
                "(z.B. ``@openstack:::team``)",
                code="MARKER_EMPTY",
            )
        if source == "packer" and var_scope in ("team", "user"):
            raise MarkerError(
                var_name,
                f"packer-Variablen unterstützen nur ``var_scope = all``; "
                f"angegeben: '{var_scope}'. Begründung: Packer baut EIN "
                f"Image, das von allen späteren VMs/Teams/Usern geteilt "
                f"wird — ein Per-Team-Wert hätte keine Wirkung.",
                code="MARKER_PACKER_SCOPE_FORBIDDEN",
            )
        return (None, None, None, None, var_scope, None)

    # File-Marker hat seine eigene Slot-Semantik: der Mode-Slot trägt
    # den Scope (``all``/``team``/``user``), der Multi-Slot trägt den
    # PFLICHT-Endungsfilter (``pdf`` oder ``pdf|docx``). Wir handlen
    # das hier separat, damit die generische Mode/Multi-Logik darunter
    # unverändert bleibt.
    if os_type == "file":
        if source == "packer":
            # Packer baut ein Image — File-Variablen würden im Build
            # gar nicht ankommen (der Files-Pfad mergt heute hartcodiert
            # in ``userInputVar.terraform``). Statt einer stillen
            # Falle: Marker-Fehler.
            raise MarkerError(
                var_name,
                "``@openstack:file`` ist in Packer-Variablen nicht "
                "unterstützt — Dateien werden ausschließlich im "
                "Terraform-Pfad zugestellt",
                code="MARKER_FILE_PACKER_FORBIDDEN",
            )

        file_scope: str | None = None
        if raw_mode is not None and raw_mode != "":
            rs = raw_mode.lower()
            if rs in _FILE_SCOPES:
                file_scope = rs
            else:
                scope_suggestion = _closest_match(rs, _FILE_SCOPES)
                hint = f"; meintest du '{scope_suggestion}'?" if scope_suggestion else ""
                raise MarkerError(
                    var_name,
                    f"ungültiger file-scope '{raw_mode}'{hint} — erwartet "
                    f"{sorted(_FILE_SCOPES)}",
                    code="MARKER_INVALID_FILE_SCOPE",
                )

        # Multi-Slot ist jetzt der Pflicht-Extensions-Filter. Ein leerer
        # Slot ist Fehler — File-Variablen brauchen eine explizite
        # Erlaubnisliste, damit der Wizard im ``accept``-Attribut filtern
        # kann und der Backend-Upload einen klaren Validierungspfad hat.
        #
        # Regex-Detail: bei Werten mit ``|`` (z.B. ``pdf|docx``) landet
        # der Inhalt im vierten Slot statt im dritten, weil der dritte
        # Slot keine Pipe akzeptiert. Wir akzeptieren das transparent
        # — beide Positionen werden auf den Extensions-Inhalt geprüft.
        exts_slot: str | None = None
        if raw_multi not in (None, ""):
            exts_slot = raw_multi
            if raw_scope not in (None, ""):
                raise MarkerError(
                    var_name,
                    f"@openstack:file akzeptiert keinen fünften Slot "
                    f"(angegeben: '{raw_scope}') — der Scope steht im "
                    f"dritten Slot (z.B. ``@openstack:file:user:pdf``)",
                    code="MARKER_FILE_EXTRA_SLOT",
                )
        elif raw_scope not in (None, ""):
            exts_slot = raw_scope
        if exts_slot is None:
            raise MarkerError(
                var_name,
                "``@openstack:file`` braucht einen Endungsfilter im "
                "vierten Slot, z.B. ``@openstack:file:all:pdf`` oder "
                "``@openstack:file:user:pdf|docx``",
                code="MARKER_FILE_MISSING_EXTENSIONS",
            )
        exts_raw = exts_slot.lower()
        if not _FILE_EXTENSIONS_RE.match(exts_raw):
            raise MarkerError(
                var_name,
                f"ungültiger Endungsfilter '{exts_slot}' — erlaubt sind "
                f"alphanumerische Endungen, mehrere getrennt mit '|' "
                f"(z.B. ``pdf|docx``)",
                code="MARKER_FILE_INVALID_EXTENSIONS",
            )
        file_exts = exts_raw.split("|")

        return (os_type, None, None, file_scope, file_scope, file_exts)

    mode: str | None = None
    if raw_mode is not None:
        rm = raw_mode.lower()
        if rm == "":
            # Leerer Slot ist erlaubt: ``@openstack:flavor::multi`` heißt
            # „mode = default, multi = multi". Wir lassen ``mode = None``,
            # die Defaults werden vom Caller appliziert.
            pass
        elif rm in ("id", "name"):
            mode = rm
        elif rm in ("multi", "list", "single"):
            # Häufiger Author-Fehler: User wollte ``:multi`` setzen aber
            # hat den Mode-Slot nicht leer gelassen. Statt einer
            # generischen „ungültiger mode"-Meldung den korrekten
            # Marker zeigen.
            raise MarkerError(
                var_name,
                f"'{raw_mode}' ist ein multi-Flag, nicht ein Mode — "
                f"schreibe den Marker mit leerem Mode-Slot, z.B. "
                f"``@openstack:{os_type}::{rm}``",
                code="MARKER_MULTI_IN_MODE_SLOT",
            )
        elif rm in _VAR_SCOPES:
            # var-scope-in-mode-slot: gleiche Logik wie für
            # multi-in-mode-slot. Der App-Autor wollte den
            # ``var_scope`` setzen, hat aber die mittleren Slots
            # nicht leer gelassen (``@openstack:flavor:team`` statt
            # ``@openstack:flavor:::team``). Statt einer kryptischen
            # „ungültiger mode"-Meldung den korrekten Marker zeigen.
            raise MarkerError(
                var_name,
                f"'{raw_mode}' ist ein var_scope, nicht ein Mode — "
                f"schreibe den Marker mit leerem Mode-/Multi-Slot, z.B. "
                f"``@openstack:{os_type}:::{rm}``",
                code="MARKER_SCOPE_IN_MODE_SLOT",
            )
        else:
            mode_suggestion = _closest_match(rm, {"id", "name"})
            hint = f"; meintest du '{mode_suggestion}'?" if mode_suggestion else ""
            raise MarkerError(
                var_name,
                f"ungültiger mode '{raw_mode}'{hint} — erwartet 'id' oder 'name'",
                code="MARKER_INVALID_MODE",
            )

    multi: bool | None = None
    if raw_multi is not None:
        mm = raw_multi.lower()
        if mm == "":
            pass
        elif mm in ("multi", "list"):
            # ``list`` ist Synonym für ``multi`` — siehe Modul-Docstring.
            multi = True
        elif mm == "single":
            multi = False
        else:
            multi_suggestion = _closest_match(mm, {"multi", "list", "single"})
            hint = f"; meintest du '{multi_suggestion}'?" if multi_suggestion else ""
            raise MarkerError(
                var_name,
                f"ungültiger multi-Flag '{raw_multi}'{hint} — erwartet "
                "'multi', 'list' oder 'single'",
                code="MARKER_INVALID_MULTI",
            )

    # Konsistenz-Check: explizite Marker-Werte gegen HCL-Type.
    # ``list``/``set``/``tuple`` sind die kollektion-fähigen Picker-Types
    # — bei diesen ist ``:multi`` natürlich. ``map``/``object`` sind
    # technische Kollektionen, aber der Picker kann sie nicht sinnvoll
    # bedienen — wir behandeln sie wie single-strings (für die
    # Konflikt-Erkennung). Wer eine map mit ``:multi`` will, kriegt
    # einen Konflikt-Error mit klarem Wording.
    type_lower = (var_type or "").strip().lower()

    # Bug #1 — ``:multi:team`` unreachable: bei scope team/user verlangt
    # die Wizard-Vertragsschnittstelle eine ``map(...)`` als HCL-Type.
    # Der ``is_collection_type``-Check würde hier zwangsläufig fehlschlagen
    # (``map(list(string))`` startet mit ``map(``), obwohl die innere
    # Element-Type eine echte Kollektion ist. Wir packen für scoped
    # Marker den ``map(...)`` aus und prüfen den INNEREN Typ gegen die
    # multi-Erwartung.
    var_scope_for_check = _parse_var_scope(raw_scope)
    type_for_collection_check = type_lower
    if var_scope_for_check in ("team", "user") and type_lower.startswith("map("):
        # Klammer-balanciert das innere Stück aus ``map(...)`` extrahieren.
        # Naive ``[4:-1]``-Slicing reicht hier nicht, weil verschachtelte
        # ``map(map(...))`` legitim sind — wir laufen einmal über die
        # Zeichen und zählen Klammern.
        depth = 0
        start = type_lower.find("(")
        inner_end = -1
        for i in range(start, len(type_lower)):
            ch = type_lower[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    inner_end = i
                    break
        if inner_end > start + 1:
            type_for_collection_check = type_lower[start + 1:inner_end].strip()

    is_collection_type = (
        type_for_collection_check.startswith(("list(", "set(", "tuple("))
        or type_for_collection_check in ("list", "set")
    )
    if multi is True and not is_collection_type and type_for_collection_check not in ("", "string"):
        # ``string`` lassen wir durchgehen, weil viele Apps ``type = string``
        # ohne Multi-Marker meinen und das Frontend dann eh CSV liefert.
        # Aber etwa ``type = number`` oder ``type = map(...)`` mit
        # ``:multi`` ist offensichtlich widersprüchlich.
        raise MarkerError(
            var_name,
            f"marker deklariert ':multi', aber HCL-Type ist '{var_type}' "
            "— erlaubt sind nur ``string``, ``list(...)``, ``set(...)`` "
            "und ``tuple(...)``",
            code="MARKER_MULTI_TYPE_CONFLICT",
        )
    if multi is False and is_collection_type:
        raise MarkerError(
            var_name,
            f"marker deklariert ':single', aber HCL-Type ist '{var_type}' "
            "(eine list/set/tuple-Kollektion) — fixe einen der beiden",
            code="MARKER_SINGLE_TYPE_CONFLICT",
        )

    # Vierter Slot bei non-file-Markern: der allgemeine ``var_scope``.
    # Validierung passiert in ``_parse_var_scope`` (definiert oben in
    # diesem Funktions-Body); Default-Auflösung in ``_apply_defaults``.
    # Wir haben den scope bereits oben für den ``is_collection_type``-
    # Inner-Type-Lookup geparst (Bug #1) — Re-Use, damit dieselbe
    # Fehlermeldung nicht zweimal feuert.
    var_scope = var_scope_for_check
    if source == "packer" and var_scope in ("team", "user"):
        raise MarkerError(
            var_name,
            f"packer-Variablen unterstützen nur ``var_scope = all``; "
            f"angegeben: '{var_scope}'. Begründung: Packer baut EIN "
            f"Image, das von allen späteren VMs/Teams/Usern geteilt "
            f"wird — ein Per-Team-Wert hätte keine Wirkung.",
            code="MARKER_PACKER_SCOPE_FORBIDDEN",
        )

    return (os_type, mode, multi, None, var_scope, None)


def _apply_defaults(
    os_type: str, mode: str | None, multi: bool | None, var_type: str
) -> tuple[str, bool]:
    """
    Wendet die dokumentierten Defaults an, wenn der Marker einzelne
    Slots leer lässt:

    - ``mode``: 'name'. Für Resourcen aus ``_NAME_ONLY_TYPES`` (Keypair,
      Availability Zone, Floating-IP-Pool) ist 'name' nicht nur Default,
      sondern faktisch die einzige sinnvolle Wahl — das Set ist trotzdem
      nur informational, weil ``:id`` für diese Typen zwar respektiert
      wird, aber kaum nutzbare Resultate liefert.
    - ``multi``: aus HCL-Type abgeleitet — ``list(...)``/``set(...)``/
      ``tuple(...)``/``list``/``set`` → True, sonst False.
    """
    if mode is None:
        mode = "name"

    if multi is None:
        type_lower = (var_type or "").strip().lower()
        # ``map(...)``/``object({...})`` sind technisch Kollektionen,
        # aber der Picker kann sie nicht sinnvoll bedienen — wir
        # behandeln sie als "single" und überlassen es dem Autor, das
        # mit einem expliziten ``:multi`` zu fordern, falls er das wirklich
        # will. ``list``/``set``/``tuple`` werden als multi auto-detected.
        multi = (
            type_lower.startswith(("list(", "set(", "tuple("))
            or type_lower in ("list", "set")
        )

    return (mode, multi)


def _closest_match(s: str, candidates: set[str]) -> str | None:
    """
    Sehr einfache Levenshtein-1-Heuristik für „meintest du …?"-Hints.
    Wir laden ``difflib`` lazy, weil das die einzige Stelle ist, wo wir
    es brauchen.
    """
    if not s:
        return None
    import difflib
    matches = difflib.get_close_matches(s, candidates, n=1, cutoff=0.7)
    return matches[0] if matches else None


def _line_number_at(content: str, char_index: int) -> int:
    """1-basierter Zeilen-Index für eine Char-Position. Wir benutzen
    das, um in MarkerError-Messages auf die Zeile in ``variables.tf``
    zu zeigen, statt nur den Variablennamen zu nennen — Apps mit 30+
    Variablen sind sonst grep-Arbeit für den Autor."""
    return content.count("\n", 0, char_index) + 1


def _validate_file_var_shape(var_name: str, var_type: str, scope: str) -> None:
    """Verify a ``@openstack:file:<scope>``-marked variable has the
    HCL type the wizard contract expects.

    The contract — documented in the deploy/file-uploads design — is:

    * ``scope = all``  → ``map(object({...}))``
    * ``scope = team`` → ``map(map(object({...})))``
    * ``scope = user`` → ``map(map(object({...})))``

    The outer map keys content by upload-key (today always
    ``"uploaded"``, reserved for future multi-file-per-slot). For
    ``team``/``user`` the next layer keys by team name resp.
    ``Team-User``-pair so the worker can route per-recipient bytes.

    We don't try to parse HCL — we just check the prefix shape with
    cheap string ops. False positives are unlikely (no real-world HCL
    type accidentally starts with ``map(map(`` unless it is one) and
    a strict full parse would be a big dependency for one check.
    """
    type_normalised = (var_type or "").strip().lower().replace(" ", "")
    if scope == "all" and not type_normalised.startswith("map(object("):
        raise MarkerError(
            var_name,
            f"marker ``@openstack:file:all`` erwartet HCL-Type "
            f"``map(object({{name=string, content_b64=string, "
            f"size=number, content_type=string}}))`` — angegeben: '{var_type}'",
            code="MARKER_FILE_TYPE_SHAPE",
        )
    if scope in ("team", "user") and not type_normalised.startswith("map(map(object("):
        raise MarkerError(
            var_name,
            f"marker ``@openstack:file:{scope}`` erwartet HCL-Type "
            f"``map(map(object({{name=string, content_b64=string, "
            f"size=number, content_type=string}})))`` — angegeben: '{var_type}'",
            code="MARKER_FILE_TYPE_SHAPE",
        )


def _validate_scoped_var_shape(var_name: str, var_type: str, scope: str) -> None:
    """Verify a non-file variable marked with ``var_scope = team|user``
    has a map-typed HCL declaration.

    Reasoning: bei ``team``/``user``-Scope schickt der Wizard eine Map
    (slot_key → value) an Terraform/Packer. Wenn der HCL-Type ein
    Skalar ist (``string``, ``number``, ...), würde Terraform die Map
    beim Apply ablehnen. Wir fangen das hier ab, damit der App-Autor
    den Fehler bei ``GET /apps/{id}/variables`` sieht und nicht erst
    beim ersten Deploy.

    Bei ``scope = all`` (oder fehlendem Scope) gilt das nicht — dann
    rendert der Wizard genau EIN Eingabefeld, das wie heute direkt
    als Skalar oder Liste an Terraform durchgereicht wird.
    """
    if scope not in ("team", "user"):
        return
    type_normalised = (var_type or "").strip().lower().replace(" ", "")
    if not type_normalised.startswith("map(") and type_normalised not in ("map",):
        raise MarkerError(
            var_name,
            f"marker deklariert ``var_scope = {scope}``, aber HCL-Type "
            f"ist '{var_type}'. Pro Scope-Eintrag liefert der Wizard "
            f"eine Map (slot_key → value), also muss der HCL-Type "
            f"``map(...)`` sein — z.B. ``map(string)`` oder "
            f"``map(list(string))``.",
            code="MARKER_SCOPED_REQUIRES_MAP",
        )


def _coerce_hcl_default(raw_default: str, var_type: str) -> tuple[Any, bool]:
    """Bug #7 — HCL-Default-Literale in das passende Python-Pendant
    überführen, damit das Frontend ``default = 2`` als ``2`` (Zahl)
    sieht statt als ``"2"`` (String). Liefert ``(value, required)`` —
    bei einem HCL-``null``-Default ist der Wert ``None`` UND
    ``required = True``, weil Terraform null als „kein Default" wertet.

    Robust gegenüber Mini-Whitespace und Trailing-Kommata; bei jedem
    Parse-Fehler fallen wir auf den rohen String zurück (lieber ein
    String im Frontend als ein 500er-Endpoint).
    """
    if raw_default is None:
        return (None, True)

    stripped = raw_default.strip()
    if stripped == "":
        return (None, True)

    # Literal HCL ``null`` → Variable ist required.
    if stripped.lower() == "null":
        return (None, True)

    type_lower = (var_type or "").strip().lower()

    # Bool zuerst, weil ``"true"`` als String-Default sonst durch den
    # Stringpfad gefangen wird.
    if type_lower == "bool":
        if stripped.lower() == "true":
            return (True, False)
        if stripped.lower() == "false":
            return (False, False)

    if type_lower == "number":
        try:
            if "." in stripped or "e" in stripped.lower():
                return (float(stripped), False)
            return (int(stripped), False)
        except ValueError:
            return (stripped, False)

    is_list_like = (
        type_lower.startswith(("list(", "set(", "tuple("))
        or type_lower in ("list", "set")
    )
    is_map_like = type_lower.startswith("map(") or type_lower in ("map", "object")

    if is_list_like or is_map_like or stripped.startswith(("[", "{")):
        # python-hcl2 wäre die saubere Variante; verfügbar ist es im
        # Backend aktuell nicht und ein Lazy-Import würde den Import-
        # Pfad fragil machen. Stattdessen json.loads — HCL-Literale
        # für Listen/Maps mit String/Number/Bool-Werten sind eine
        # echte Teilmenge von JSON.
        import json
        try:
            return (json.loads(stripped), False)
        except (ValueError, TypeError):
            # Fallback: HCL erlaubt unquoted Identifier als String
            # (``["NAT"]`` ist häufig, aber auch ``[NAT]``) und
            # ``true``/``false``/``null`` als Werte. Wir versuchen
            # einen behutsamen Pre-Tokenize-Schritt; bei weiterem
            # Fehlschlag fällt der String unverändert durch.
            try:
                normalised = re.sub(
                    r"\b(true|false|null)\b",
                    lambda m: m.group(0).lower(),
                    stripped,
                    flags=re.IGNORECASE,
                )
                return (json.loads(normalised), False)
            except (ValueError, TypeError):
                return (stripped, False)

    # String (oder unbekannter Type): äußere Quotes abstreifen, falls
    # der Caller das nicht schon getan hat.
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ('"', "'"):
        return (stripped[1:-1], False)
    return (stripped, False)


def _parse_one_variable(
    *,
    var_name: str,
    var_block: str,
    var_block_offset: int,
    file_content: str,
    file_label: str,
    source: str,
) -> dict[str, Any]:
    """
    Verarbeitet einen einzelnen ``variable "..." { ... }``-Block.

    Liefert das Variable-Dict immer; etwaige Marker-Fehler werden NICHT
    geworfen, sondern im Feld ``markerError`` an die Variable angehängt.
    Damit kann das Frontend die Variable als Free-Text rendern und den
    Fehler inline zeigen — die App bleibt benutzbar, der Autor sieht
    den Fehler aber sofort. Globaler 400-Abbruch wäre die schlechte
    Variante (1 schlechter Marker → ganzer Wizard kaputt).
    """
    # Extract type
    type_match = re.search(r'type\s*=\s*([^\n]+)', var_block)
    var_type = type_match.group(1).strip() if type_match else "string"

    # Extract description
    desc_match = re.search(r'description\s*=\s*"([^"]*)"', var_block)
    description = desc_match.group(1) if desc_match else ""

    # Extract default value
    default_match = re.search(r'default\s*=\s*([^\n]+)', var_block)
    default_raw = default_match.group(1).strip() if default_match else None

    # Bug #7 — HCL-Defaults nicht als rohe Strings durchreichen, sondern
    # in das passende Python-Pendant coercen (number→int/float, bool→
    # bool, list/map→Python-Listen/-Dicts). ``null`` setzt ``required``
    # zurück auf True. Bei Parse-Fehler fällt der Wert auf den
    # ursprünglichen String zurück.
    try:
        default_value, required = _coerce_hcl_default(default_raw, var_type)
    except Exception:
        # Defensiv: kein einziger HCL-Edge-Case sollte den Wizard
        # crashen. Im Worst-Case bleibt das alte Verhalten erhalten —
        # roher String, required=False wenn ein Default da war.
        default_value = default_raw
        required = default_raw is None

    var_info: dict[str, Any] = {
        "name": var_name,
        "type": var_type,
        "description": description,
        "default": default_value,
        "required": required,
        "source": source,
    }

    # @openstack-Marker auswerten. Per-Variable-Try/Except: ein Tippfehler
    # in EINER Variablen-Description darf nicht den gesamten Wizard
    # blockieren; der Fehler reist im Payload mit der Variablen mit.
    try:
        (
            os_type,
            raw_mode,
            raw_multi,
            file_scope,
            var_scope,
            file_exts,
        ) = _parse_marker(var_name, var_type, description, source=source)
        # File-Variablen haben eine harte Vertragsschnittstelle gegenüber
        # cloud-init: der Wizard muss wissen, ob er einen Single-Slot
        # (scope=all), eine Map über Teams oder eine Map über User
        # rendern soll. Die HCL-Type-Schachtelung muss zum Scope
        # passen, sonst würde Terraform den Decode beim Apply
        # zurückweisen — wir fangen das hier ab und geben dem Autor
        # eine klare Fehlermeldung statt eines stack-traces im
        # Worker-Log.
        if os_type == "file":
            _validate_file_var_shape(var_name, var_type, file_scope or "all")
        elif var_scope:
            _validate_scoped_var_shape(var_name, var_type, var_scope)
    except MarkerError as exc:
        line = _line_number_at(file_content, var_block_offset)
        var_info["markerError"] = {
            "variable": exc.var_name,
            "message": exc.message,
            "location": f"{file_label}:{line}",
            # ``code`` ist der stabile Schlüssel für künftige i18n /
            # Frontend-Logik. Existierende Clients ignorieren das Feld.
            "code": exc.code,
        }
        return var_info

    if os_type:
        if os_type == "file":
            # File-Variablen sind weder mode- noch multi-driven; der
            # Wizard rendert eine FileDropZone, nicht den Resource-
            # Picker. ``osMode`` und ``osMulti`` werden bewusst nicht
            # gesetzt, damit das Frontend eine fehlende Belegung als
            # „nicht-anwendbar" liest statt einen Default-Wert zu
            # erfinden.
            var_info["osType"] = os_type
            var_info["osScope"] = file_scope or "all"
            if file_exts:
                var_info["fileExtensions"] = file_exts
        else:
            mode, multi = _apply_defaults(os_type, raw_mode, raw_multi, var_type)
            var_info["osType"] = os_type
            var_info["osMode"] = mode
            var_info["osMulti"] = multi

    # ``varScope`` ist orthogonal zum Resource-Type — auch eine
    # Free-Text-Variable (kein ``osType``) kann scoped sein.
    # Für File-Variablen spiegeln wir den ``osScope`` zusätzlich in
    # ``varScope``, damit das Frontend für Slot-Berechnung nur EINE
    # Quelle lesen muss.
    if var_scope:
        var_info["varScope"] = var_scope
    elif os_type == "file":
        var_info["varScope"] = file_scope or "all"

    return var_info


def _parse_terraform_variables(file_path: str) -> list[dict[str, Any]]:
    """Parse Terraform `variables.tf` file. Marker-Fehler einzelner
    Variablen werden im Variable-Payload als ``markerError`` mitgesendet
    (nicht geworfen) — siehe ``_parse_one_variable``."""
    with open(file_path) as f:
        content = f.read()

    variables = []
    # Regex to match variable blocks: variable "name" { ... }
    pattern = r'variable\s+"([^"]+)"\s*\{([^}]+)\}'

    for match in re.finditer(pattern, content, re.DOTALL):
        var_name = match.group(1)
        var_block = match.group(2)
        # Filter: users und image_name rauslassen
        if var_name == "users" or var_name == "image_name":
            continue
        # Multi-image apps declare ``image_name_<key>`` per template and
        # mark those declarations with ``@platform:internal`` in the
        # description. The worker fills these from the discovered Packer
        # templates; the wizard must not surface them as user-editable
        # variables. Same rationale as the ``image_name``/``users``
        # filter above — these are platform-injected, not user input.
        desc_match = re.search(r'description\s*=\s*"([^"]*)"', var_block)
        description = desc_match.group(1) if desc_match else ""
        if "@platform:internal" in description:
            continue
        variables.append(_parse_one_variable(
            var_name=var_name,
            var_block=var_block,
            var_block_offset=match.start(),
            file_content=content,
            file_label="terraform/variables.tf",
            source="terraform",
        ))

    return variables


def _parse_packer_variables(file_path: str, template_key: str = "default") -> list[dict[str, Any]]:
    """Parse Packer `variables.pkr.hcl` file. Marker-Fehler reisen pro
    Variable im ``markerError``-Feld mit; siehe ``_parse_one_variable``.

    ``template_key`` is recorded on each variable so the wizard can
    group Packer variables per template (and avoid name collisions
    across templates in multi-image apps). For the legacy single-
    template layout the caller passes ``"default"``.
    """
    with open(file_path) as f:
        content = f.read()

    variables = []
    # Packer uses similar syntax: variable "name" { ... }
    pattern = r'variable\s+"([^"]+)"\s*\{([^}]+)\}'

    for match in re.finditer(pattern, content, re.DOTALL):
        var_name = match.group(1)
        var_block = match.group(2)
        # Filter: image_name rauslassen
        if var_name == "image_name":
            continue
        var_info = _parse_one_variable(
            var_name=var_name,
            var_block=var_block,
            var_block_offset=match.start(),
            file_content=content,
            file_label=f"packer/{template_key}/variables.pkr.hcl"
            if template_key != "default"
            else "packer/variables.pkr.hcl",
            source="packer",
        )
        var_info["template_key"] = template_key
        variables.append(var_info)

    return variables


# ----------------------------------------------------------------
# PACKER TEMPLATE DISCOVERY
# ----------------------------------------------------------------
# Apps may ship Packer templates in one of two layouts:
#
#  1. Legacy single-template layout:
#         packer/template.pkr.hcl
#         packer/variables.pkr.hcl
#     → exactly ONE image, conventionally keyed ``default``. The
#       worker injects ``image_name`` (a single Terraform variable).
#
#  2. Multi-template layout:
#         packer/<key>/template.pkr.hcl
#         packer/<key>/variables.pkr.hcl   (optional)
#     → one image per ``<key>``. The worker injects one
#       ``image_name_<key>`` Terraform variable per template, each
#       marked ``@platform:internal`` in its description so the wizard
#       skips them.
#
# Discovery rules:
#   - No ``packer/`` directory → returns ``[]`` (no Packer phase).
#   - Legacy file present       → returns ``[_PackerTemplate("default", ...)]``.
#   - Subdirectories with a
#     ``template.pkr.hcl``      → returns one entry per subdir, sorted.
#   - Both legacy AND subdirs   → ``PackerTemplateDiscoveryError`` (hard).
#   - Subdir without
#     ``template.pkr.hcl``      → ignored (e.g. ``_common/``, ``scripts/``).
#   - Subdir with a key that
#     doesn't match the pattern → ``PackerTemplateDiscoveryError``.
#
# Key pattern is intentionally narrow (``[a-z][a-z0-9_-]{0,30}``) so
# the key is safe to embed in Terraform variable names and image
# tags without quoting.
# ----------------------------------------------------------------

@dataclass
class _PackerTemplate:
    """One Packer template discovered under ``<repo>/packer``.

    ``variables_path`` may point at a non-existing file — the caller
    must check ``os.path.isfile`` before reading it. We don't filter
    here because the file is optional and a missing one is not an
    error.
    """

    key: str
    template_path: str
    variables_path: str


_TEMPLATE_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")


class PackerTemplateDiscoveryError(ValueError):
    """Raised when the Packer directory has a layout the platform can't
    reconcile (ambiguous, contradictory, or with an unsafe key).

    Translated to HTTP 422 at the load_variable_definitions boundary
    so the app author sees the error immediately on the first
    ``GET /apps/{id}/variables`` instead of at first deploy.
    """


def _discover_packer_templates(repo_path: str) -> list[_PackerTemplate]:
    """Walk ``<repo_path>/packer`` and return the list of templates the
    worker will build for this app.

    See the section docstring above for the layout rules. Returns
    ``[]`` for apps without any Packer at all (Terraform-only).
    """
    packer_dir = os.path.join(repo_path, "packer")
    if not os.path.isdir(packer_dir):
        return []

    legacy_template = os.path.join(packer_dir, "template.pkr.hcl")
    has_legacy = os.path.isfile(legacy_template)

    multi_templates: list[_PackerTemplate] = []
    bad_keys: list[str] = []
    for entry in sorted(os.listdir(packer_dir)):
        sub = os.path.join(packer_dir, entry)
        if not os.path.isdir(sub):
            continue
        tmpl = os.path.join(sub, "template.pkr.hcl")
        if not os.path.isfile(tmpl):
            # Subdirs without a template (``_common/``, ``scripts/``,
            # ``http/`` for boot-time HTTP servers, ...) are silently
            # ignored — they're tooling, not images to build.
            continue
        if not _TEMPLATE_KEY_RE.match(entry):
            bad_keys.append(entry)
            continue
        multi_templates.append(_PackerTemplate(
            key=entry,
            template_path=tmpl,
            variables_path=os.path.join(sub, "variables.pkr.hcl"),
        ))

    if bad_keys:
        raise PackerTemplateDiscoveryError(
            f"Packer template subdirectories with invalid keys "
            f"(must match [a-z][a-z0-9_-]{{0,30}}): {bad_keys}"
        )

    if has_legacy and multi_templates:
        raise PackerTemplateDiscoveryError(
            "App repository has BOTH packer/template.pkr.hcl (legacy "
            "layout) AND packer/<key>/template.pkr.hcl subdirectories "
            f"({[t.key for t in multi_templates]}). Choose one layout "
            "— remove the legacy file or the subdirectories."
        )

    if has_legacy:
        return [_PackerTemplate(
            key="default",
            template_path=legacy_template,
            variables_path=os.path.join(packer_dir, "variables.pkr.hcl"),
        )]

    return multi_templates


# ----------------------------------------------------------------
# GET ALL APPS
# ----------------------------------------------------------------
@router.get("/", response_model=list[AppResponse])
def list_apps(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """List apps visible to the current user.

    - Admins and teachers see all non-deleted apps.
    - Regular users see their own apps plus public apps that have at
      least one approved version.
    """
    if current_user.role in (UserRole.ADMIN, UserRole.TEACHER):
        apps = crud_apps.get_apps(db, skip=skip, limit=limit)
    else:
        apps = crud_apps.get_visible_apps(db, current_user.userId, skip=skip, limit=limit)
    return [_serialize_app(a) for a in apps]


# ----------------------------------------------------------------
# GET APP BY ID
# ----------------------------------------------------------------
@router.get("/{app_id}", response_model=AppWithVersions)
def get_app(
    app_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """Get app by ID with available versions.

    Soft-deleted apps are still readable here so existing deployments
    that still reference them can render their app name, git link,
    etc. They just don't show up in the apps list / deploy wizard
    (those use the default-filtered ``get_apps``).
    """
    app = crud_apps.get_app(db, app_id, include_deleted=True)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )

    # Check access permission:
    # - Owner, Teacher, Admin: always allowed
    # - Everyone else: only if app is public AND has at least one approved version
    is_owner_or_staff = (
        str(app.userId) == str(current_user.userId)
        or current_user.role in (UserRole.TEACHER, UserRole.ADMIN)
    )
    if not is_owner_or_staff and (app.is_private or not crud_approvals.has_any_approved_version(db, app.appId)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to access this resource"
            )

    # Fetch versions if git_link exists. Skipped for soft-deleted apps
    # — listing versions is a "what could I deploy" affordance and the
    # answer is "nothing", you already deleted this app.
    if app.git_link and app.deleted_at is None:
        try:
            all_versions = git_service.get_versions(app.git_link)
            if is_owner_or_staff:
                # Owner/Teacher/Admin see all Git tags
                app.versions = all_versions
            else:
                # Everyone else only sees approved version tags
                approved_tags = {
                    a.version_tag
                    for a in crud_approvals.get_approvals_for_app(db, app.appId)
                    if a.status == "approved"
                }
                app.versions = [
                    v for v in all_versions
                    if (v if isinstance(v, str) else v.get("version") or v.get("releaseTag") or v.get("tag", "")) in approved_tags
                ]
        except Exception as e:
            app.versions = []
            import logging
            logging.getLogger(__name__).warning(f"Could not fetch versions: {str(e)}")
    else:
        app.versions = []

    return _serialize_app(app)


# ----------------------------------------------------------------
# GET APP VARIABLES
# ----------------------------------------------------------------
def load_variable_definitions(app, version: str) -> list[dict[str, Any]]:
    """Clone the app's release-vars and parse all Terraform/Packer
    variables into the same shape ``GET /apps/{id}/variables`` returns.

    Reusable from ``POST /deployments`` so the deployment endpoint can
    enforce per-variable contracts (``varScope``, ``fileExtensions``)
    using the App-Autor's declarations as source-of-truth. Cleans up
    the temporary clone on its own — callers don't manage paths.

    Raises ``HTTPException(400)`` if the app has no Git link and
    bubbles unexpected errors as ``HTTPException(500)``.
    """
    logger = logging.getLogger(__name__)
    if not app.git_link:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="App has no Git repository configured",
        )

    deployment_id = f"vars_{app.appId}_{version}".replace("/", "_")
    repo_path = None
    try:
        repo_path = git_service.clone_release_vars(app.git_link, version, deployment_id)
        variables: list[dict[str, Any]] = []
        tf_vars_path = os.path.join(repo_path, "terraform", "variables.tf")
        if os.path.exists(tf_vars_path):
            variables.extend(_parse_terraform_variables(tf_vars_path))
        # Discover all Packer templates (legacy single-file layout OR
        # per-key subdirectories) and parse each one's variables. The
        # ``template_key`` is recorded on every Packer variable so the
        # wizard can group inputs per image. Discovery raises if the
        # repo has an ambiguous or unsafe layout — surface that as
        # HTTP 422 so the app author can fix the repo before any
        # deploy attempt.
        try:
            templates = _discover_packer_templates(repo_path)
        except PackerTemplateDiscoveryError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )
        for tmpl in templates:
            if os.path.isfile(tmpl.variables_path):
                variables.extend(
                    _parse_packer_variables(tmpl.variables_path, template_key=tmpl.key)
                )
        return variables
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Failed to load variable definitions for app %s version %s",
            app.appId, version,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch variables",
        )
    finally:
        if repo_path:
            try:
                git_service.cleanup_repository(repo_path)
            except Exception as cleanup_error:
                logger.error(
                    "Failed to cleanup repository: %s", str(cleanup_error)
                )


@router.get("/{app_id}/variables", response_model=list[AppVariableResponse])
def get_app_variables(
    app_id: UUID,
    version: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Get dynamic app variables from app's Git repository
    Parses variables.tf file and returns all configurable variables

    Returns:
    - name: Variable name
    - type: Variable type (string, number, bool, list, map, etc.)
    - description: Variable description
    - default: Default value (if any)
    - required: Whether variable is required
    """
    app = crud_apps.get_app(db, app_id)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )

    # Check access permission
    ensure_resource_access(app.userId, current_user)

    logger = logging.getLogger(__name__)
    variables = load_variable_definitions(app, version)
    if not variables:
        logger.warning("No variables found for app %s version %s", app_id, version)

    # Marker-Fehler reisen pro Variable im ``markerError``-Feld mit
    # — der Endpoint wirft kein 400 mehr für einzelne Marker-Bugs,
    # sondern überlässt dem Frontend die Anzeige inline. Ein bug-
    # behaftetes Marker → eine Variable als Free-Text + Inline-Hint;
    # alle anderen Variablen bleiben benutzbar.
    bad = [v for v in variables if v.get("markerError")]
    if bad:
        logger.warning(
            "App %s version %s has %d variable(s) with bad @openstack markers: %s",
            app_id, version, len(bad), [v["name"] for v in bad],
        )

    return variables


# ----------------------------------------------------------------
# CREATE APP
# ----------------------------------------------------------------
@router.post("/", response_model=AppResponse, status_code=status.HTTP_201_CREATED)
def create_app(
    app: AppCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """
    Create a new app
    - **All authenticated users** can create apps
    - **Git repository access is verified** before creating the app
    """
    logger = logging.getLogger(__name__)
    # Decode the optional image data-URL up front so a malformed
    # payload fails before we hit Keycloak / Git / DB.
    image_bytes, image_mime = parse_image_data_url(app.image)

    # Verify repository access if git_link is provided
    if app.git_link:
        access_result = git_service.verify_repository_access(app.git_link)
        if not access_result['success']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=access_result['message']
            )
        logger.info(f"Repository access verified for {app.git_link}")

    db_app = crud_apps.create_app(db, app, current_user.userId)
    if image_bytes is not None:
        db_app = crud_apps.set_app_image(db, db_app.appId, image_bytes, image_mime)

    # Auto-submit all tags for review if requested (public apps only)
    if app.submit_all_versions and not app.is_private and app.git_link:
        try:
            versions = git_service.get_versions(app.git_link)
            for v in versions:
                tag = v.get("version") or v.get("releaseTag") or v.get("tag")
                if tag:
                    with contextlib.suppress(Exception):
                        crud_approvals.submit_version(db, app_id=db_app.appId, version_tag=tag)
        except Exception as e:
            logger.warning(f"Could not auto-submit versions for app {db_app.appId}: {e}")

    return _serialize_app(db_app)


# ----------------------------------------------------------------
# UPDATE APP
# ----------------------------------------------------------------
@router.put("/{app_id}", response_model=AppResponse)
def update_app(
    app_id: UUID,
    app_update: AppUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """Update an app.

    ``git_link`` is immutable after creation — sending it in the body
    returns HTTP 400. Use ``is_private`` to toggle visibility.
    Owner or Teacher/Admin only.
    """
    app = crud_apps.get_app(db, app_id)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )

    # Check access permission
    ensure_resource_access(app.userId, current_user)

    image_was_provided = "image" in app_update.model_fields_set
    image_bytes, image_mime = (None, None)
    if image_was_provided:
        image_bytes, image_mime = parse_image_data_url(app_update.image)

    updated_app = crud_apps.update_app(db, app_id, app_update)
    if image_was_provided:
        updated_app = crud_apps.set_app_image(db, app_id, image_bytes, image_mime)
    return _serialize_app(updated_app)


# ----------------------------------------------------------------
# SUBMIT VERSION FOR REVIEW
# ----------------------------------------------------------------
@router.post(
    "/{app_id}/versions/{version_tag}/submit",
    response_model=AppVersionApprovalResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_version(
    app_id: UUID,
    version_tag: str,
    body: AppVersionApprovalSubmit,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak),
):
    """Submit a specific version tag for admin review.

    Only the app owner can submit versions. Admins and teachers may
    submit on behalf of any app via the admin router instead.
    A REJECTED version can be resubmitted; PENDING and APPROVED cannot.
    """
    app = crud_apps.get_app(db, app_id)
    if not app:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")

    ensure_resource_access(app.userId, current_user)

    if not app.git_link:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="App has no git repository configured",
        )

    return crud_approvals.submit_version(
        db, app_id=app_id, version_tag=version_tag, diff_url=body.diff_url, notes=body.notes
    )


# ----------------------------------------------------------------
# WITHDRAW VERSION SUBMISSION
# ----------------------------------------------------------------
@router.delete(
    "/{app_id}/versions/{version_tag}/submit",
    status_code=status.HTTP_204_NO_CONTENT,
)
def withdraw_version(
    app_id: UUID,
    version_tag: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak),
):
    """Withdraw a PENDING version submission.

    Only the app owner can withdraw. Deletes the approval entry so
    the version appears as unsubmitted again.
    """
    app = crud_apps.get_app(db, app_id)
    if not app:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")

    ensure_resource_access(app.userId, current_user)
    crud_approvals.withdraw(db, app_id=app_id, version_tag=version_tag)
    return None


# ----------------------------------------------------------------
# GET VERSION APPROVALS FOR APP
# ----------------------------------------------------------------
@router.get(
    "/{app_id}/versions",
    response_model=list[AppVersionApprovalResponse],
)
def list_version_approvals(
    app_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak),
):
    """List all version approval entries for an app.

    Owner, teacher, and admin can view this list.
    """
    app = crud_apps.get_app(db, app_id, include_deleted=True)
    if not app:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")

    ensure_resource_access(app.userId, current_user)

    return crud_approvals.get_approvals_for_app(db, app_id)


# ----------------------------------------------------------------
# DELETE APP
# ----------------------------------------------------------------
@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_app(
    app_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_keycloak)
):
    """Soft-delete an app.

    Sets ``apps.deleted_at`` instead of removing the row, so any
    historical *or* still-running deployment that points at this app
    keeps resolving (the detail page can still render the app name,
    the running terraform state stays operational). The app simply
    stops appearing in listings and the deploy wizard, so no new
    deploys can be started against it. Existing deployments live on
    until the user destroys them individually.

    Owner/Teacher/Admin only (``ensure_resource_access``).
    """
    app = crud_apps.get_app(db, app_id)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )

    # Check access permission
    ensure_resource_access(app.userId, current_user)

    success = crud_apps.soft_delete_app(db, app_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    return None
