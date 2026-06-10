import logging
import os
import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import apps as crud_apps
from app.database import get_db
from app.models import User
from app.schemas import AppCreate, AppResponse, AppUpdate, AppWithVersions
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
#     @openstack:<type>[:<mode>][:<multi>]
#
#   <type>   — eine der unten gelisteten Resource-Kinds (siehe ``_OS_TYPES``)
#   <mode>   — 'id' | 'name'   (default: 'name'; siehe auch
#              ``_NAME_ONLY_TYPES`` für Resourcen, bei denen 'id' praktisch
#              sinnlos ist)
#   <multi>  — 'multi' | 'list' | 'single'  ('list' ist Synonym für 'multi'.
#              Default ohne Marker-Slot: aus dem HCL-Typ abgeleitet —
#              ``list(...)``/``set(...)``/``tuple(...)``/``list``/``set``
#              → multi, sonst single. ``map(...)`` und ``object(...)`` sind
#              technische Kollektionen, gelten hier aber als single — wer
#              die als Multi will, schreibt ``:multi`` explizit.)
#
# Beispiele:
#     @openstack:network                        → network, name-mode, multi aus HCL
#     @openstack:network:id                     → network, id-mode
#     @openstack:security_group:name:multi      → SG, name-mode, multi
#     @openstack:flavor::multi                  → leerer Mode-Slot ⇒ default 'name'
#     @openstack:image:id:single                → image, id-mode, single (auch wenn
#                                                  HCL ``list(string)`` wäre →
#                                                  Konfliktcheck schlägt zu)
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
    # multi slot is reserved for future multi-file-per-slot support and
    # must stay empty today (``:multi`` is rejected with a clear error).
    "file",
}

# Allowed scope tokens for ``@openstack:file:<scope>``. Reuses the
# mode slot of the marker grammar — keeps the regex shape unchanged
# while teaching the parser to interpret the slot per-type.
_FILE_SCOPES: set[str] = {"all", "team", "user"}

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
# Slot-Inhalt darf KEIN Whitespace haben. Vier oder mehr Doppelpunkte
# = malformed (siehe ``_TOO_MANY_SEGMENTS_RE``).
#
# Boundary-Zeichen rechts: alles was kein Identifier-Zeichen ist —
# Whitespace, Zeilenende, gängige Satzzeichen ``. , ; : ! ? ) ] " '``.
# Linke Grenze: Anfang oder dieselben Boundary-Zeichen.
_MARKER_RE = re.compile(
    r"""
    (?:^|(?<=[\s.,;:!?()\[\]"']))   # Linke Grenze: Anfang oder Whitespace/Satzzeichen
    @openstack
    :([A-Za-z][A-Za-z0-9_]*)        # 1: type
    (?::([A-Za-z]*))?               # 2: mode-Slot (kann leer sein)
    (?::([A-Za-z]*))?               # 3: multi-Slot (kann leer sein)
    (?=$|[\s.,;:!?)\]"'])           # Rechte Grenze
    """,
    re.VERBOSE,
)

# Schnell-Check: der Marker hat zu viele Segmente?
# ``@openstack:network:id:multi:extra`` → fail.
# Wir verlangen, dass JEDES der 4+ Segmente nicht-leer ist, sonst
# würde ``@openstack:network:id:multi:`` (Trailing-Colon, klare 3-Slot-
# Form) fälschlich als „zu viele Segmente" gefangen.
_TOO_MANY_SEGMENTS_RE = re.compile(
    r"@openstack(?::[A-Za-z0-9_]+){4,}",
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
    """

    def __init__(self, var_name: str, message: str):
        super().__init__(f"Variable '{var_name}': {message}")
        self.var_name = var_name
        self.message = message


def _parse_marker(
    var_name: str, var_type: str, description: str
) -> tuple[str | None, str | None, bool | None, str | None]:
    """
    Parst den ``@openstack:<type>[:<mode>][:<multi>]``-Marker aus der
    Description. Liefert ``(None, None, None, None)`` wenn KEIN Marker
    da ist (das ist KEIN Fehler — die Variable wird dann als Free-Text
    gerendert).

    Multi-Marker-Verhalten: Findet die Funktion mehrere Marker, nimmt sie
    den ersten, dessen Type bekannt ist. Das ist absichtlich tolerant —
    Apps zitieren manchmal ältere Marker-Schreibweisen in der Description
    („migration: ``@openstack:vm`` → ``@openstack:network``"). Mode/Multi-
    Validierungs-Fehler des gewählten Markers sind weiterhin hart, weil
    sie konkret und nicht-tolerierbar sind.

    Wirft ``MarkerError`` bei:
      - malformiertem Marker (zu viele Segmente, internes Whitespace,
        unbekannte mode/multi-Tokens, ``@openstack:`` ohne erkennbaren
        Type-Slot, Slot-Trenner mit Sonderzeichen statt ``:``)
      - widersprüchlichem Marker vs. HCL-Type (``:single`` mit
        ``type = list(...)`` oder ``:multi`` mit ``type = number``)
      - file-spezifisch: ungültigem Scope (``@openstack:file:foo``) oder
        nicht-leerem Multi-Slot (``@openstack:file:user:multi``).

    Returns: (os_type, mode, multi, scope). Genau einer von ``mode`` und
    ``scope`` wird je nach Type befüllt — ``mode`` für die klassischen
    OpenStack-Resourcen, ``scope`` für ``@openstack:file:<scope>``. Die
    ungenutzten Felder bleiben ``None`` und die jeweils unzutreffenden
    Defaults werden in ``_apply_defaults`` aufgesetzt.
    """
    if not description:
        return (None, None, None, None)

    # Vier+ Segmente sind nie legitim. Schnellt zuerst durch, BEVOR der
    # Haupt-Regex (der nach 3 Slots aufhört) das gar nicht mitkriegt.
    if _TOO_MANY_SEGMENTS_RE.search(description):
        raise MarkerError(
            var_name,
            "marker hat zu viele Segmente — erlaubt: "
            "@openstack:<type>[:<mode>][:<multi>]",
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
                "``@openstack:<type>[:<mode>][:<multi>]`` mit Doppelpunkten "
                "als Trenner und ohne Whitespace zwischen den Segmenten",
            )
        return (None, None, None, None)

    # Erster Marker mit BEKANNTEM Type gewinnt. Marker mit unbekanntem
    # Type werden übersprungen (toleriert): wenn Apps in der Description
    # historische Marker-Beispiele erwähnen wie ``@openstack:vm`` (kein
    # OS-Resource-Type), darf der echte ``@openstack:network`` zwei
    # Sätze später trotzdem greifen. ABER: wir merken uns den ersten
    # Marker mit gültiger Syntax aber unbekanntem Type, um eine
    # bessere Fehlermeldung zu geben, falls KEIN Match gefunden wird.
    first_unknown: tuple[str, str] | None = None  # (raw_type, suggestion)
    chosen = None
    for m in matches:
        raw_type = (m.group(1) or "")
        os_type_candidate = raw_type.lower()
        if os_type_candidate in _OS_TYPES:
            chosen = (m, raw_type, m.group(2), m.group(3))
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
        )

    _, raw_type, raw_mode, raw_multi = chosen
    os_type = raw_type.lower()

    # File-Marker hat seine eigene Slot-Semantik: der Mode-Slot trägt
    # den Scope (``all``/``team``/``user``), der Multi-Slot ist heute
    # ungenutzt und MUSS leer bleiben. Wir handlen das hier separat,
    # damit die generische Mode/Multi-Logik darunter unverändert bleibt
    # und die normalen OpenStack-Resource-Types weiterhin sauber
    # validiert werden.
    if os_type == "file":
        scope: str | None = None
        if raw_mode is not None and raw_mode != "":
            rs = raw_mode.lower()
            if rs in _FILE_SCOPES:
                scope = rs
            else:
                scope_suggestion = _closest_match(rs, _FILE_SCOPES)
                hint = f"; meintest du '{scope_suggestion}'?" if scope_suggestion else ""
                raise MarkerError(
                    var_name,
                    f"ungültiger scope '{raw_mode}'{hint} — erwartet "
                    f"{sorted(_FILE_SCOPES)}",
                )
        # Multi-Slot ist heute reserviert; jeder explizite Wert
        # (``:multi``/``:single``/Tippfehler) ist ein klarer Author-
        # Fehler. Wir lehnen ihn mit Hint ab, damit das Feld später
        # für Multi-File-pro-Slot-Support frei bleibt.
        if raw_multi not in (None, ""):
            raise MarkerError(
                var_name,
                f"@openstack:file akzeptiert keinen multi-Flag "
                f"(angegeben: '{raw_multi}') — schreib den Marker als "
                f"``@openstack:file:<scope>``",
            )
        return (os_type, None, None, scope)

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
            )
        else:
            mode_suggestion = _closest_match(rm, {"id", "name"})
            hint = f"; meintest du '{mode_suggestion}'?" if mode_suggestion else ""
            raise MarkerError(
                var_name,
                f"ungültiger mode '{raw_mode}'{hint} — erwartet 'id' oder 'name'",
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
            )

    # Konsistenz-Check: explizite Marker-Werte gegen HCL-Type.
    # ``list``/``set``/``tuple`` sind die kollektion-fähigen Picker-Types
    # — bei diesen ist ``:multi`` natürlich. ``map``/``object`` sind
    # technische Kollektionen, aber der Picker kann sie nicht sinnvoll
    # bedienen — wir behandeln sie wie single-strings (für die
    # Konflikt-Erkennung). Wer eine map mit ``:multi`` will, kriegt
    # einen Konflikt-Error mit klarem Wording.
    type_lower = (var_type or "").strip().lower()
    is_collection_type = (
        type_lower.startswith(("list(", "set(", "tuple("))
        or type_lower in ("list", "set")
    )
    if multi is True and not is_collection_type and type_lower not in ("", "string"):
        # ``string`` lassen wir durchgehen, weil viele Apps ``type = string``
        # ohne Multi-Marker meinen und das Frontend dann eh CSV liefert.
        # Aber etwa ``type = number`` oder ``type = map(...)`` mit
        # ``:multi`` ist offensichtlich widersprüchlich.
        raise MarkerError(
            var_name,
            f"marker deklariert ':multi', aber HCL-Type ist '{var_type}' "
            "— erlaubt sind nur ``string``, ``list(...)``, ``set(...)`` "
            "und ``tuple(...)``",
        )
    if multi is False and is_collection_type:
        raise MarkerError(
            var_name,
            f"marker deklariert ':single', aber HCL-Type ist '{var_type}' "
            "(eine list/set/tuple-Kollektion) — fixe einen der beiden",
        )

    return (os_type, mode, multi, None)


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
    if scope == "all":
        if not type_normalised.startswith("map(object("):
            raise MarkerError(
                var_name,
                f"marker ``@openstack:file:all`` erwartet HCL-Type "
                f"``map(object({{name=string, content_b64=string, "
                f"size=number, content_type=string}}))`` — angegeben: '{var_type}'",
            )
    elif scope in ("team", "user"):
        if not type_normalised.startswith("map(map(object("):
            raise MarkerError(
                var_name,
                f"marker ``@openstack:file:{scope}`` erwartet HCL-Type "
                f"``map(map(object({{name=string, content_b64=string, "
                f"size=number, content_type=string}})))`` — angegeben: '{var_type}'",
            )


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
    default_value = default_match.group(1).strip() if default_match else None

    # Remove surrounding quotes from string literals
    if default_value and default_value.startswith('"') and default_value.endswith('"'):
        default_value = default_value[1:-1]

    required = default_value is None

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
        os_type, raw_mode, raw_multi, scope = _parse_marker(var_name, var_type, description)
        # File-Variablen haben eine harte Vertragsschnittstelle gegenüber
        # cloud-init: der Wizard muss wissen, ob er einen Single-Slot
        # (scope=all), eine Map über Teams oder eine Map über User
        # rendern soll. Die HCL-Type-Schachtelung muss zum Scope
        # passen, sonst würde Terraform den Decode beim Apply
        # zurückweisen — wir fangen das hier ab und geben dem Autor
        # eine klare Fehlermeldung statt eines stack-traces im
        # Worker-Log.
        if os_type == "file":
            _validate_file_var_shape(var_name, var_type, scope or "all")
    except MarkerError as exc:
        line = _line_number_at(file_content, var_block_offset)
        var_info["markerError"] = {
            "variable": exc.var_name,
            "message": exc.message,
            "location": f"{file_label}:{line}",
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
            var_info["osScope"] = scope or "all"
        else:
            mode, multi = _apply_defaults(os_type, raw_mode, raw_multi, var_type)
            var_info["osType"] = os_type
            var_info["osMode"] = mode
            var_info["osMulti"] = multi

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
        variables.append(_parse_one_variable(
            var_name=var_name,
            var_block=var_block,
            var_block_offset=match.start(),
            file_content=content,
            file_label="terraform/variables.tf",
            source="terraform",
        ))

    return variables


def _parse_packer_variables(file_path: str) -> list[dict[str, Any]]:
    """Parse Packer `variables.pkr.hcl` file. Marker-Fehler reisen pro
    Variable im ``markerError``-Feld mit; siehe ``_parse_one_variable``."""
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
        variables.append(_parse_one_variable(
            var_name=var_name,
            var_block=var_block,
            var_block_offset=match.start(),
            file_content=content,
            file_label="packer/variables.pkr.hcl",
            source="packer",
        ))

    return variables


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
    """
    Get all apps owned by the current user.

    Listing is always scoped to the requester regardless of role — teachers
    and admins still only see their own apps in the index. Cross-user access
    happens explicitly through `GET /apps/{app_id}`, which is gated by
    `ensure_resource_access`.
    """
    apps = crud_apps.get_apps(db, skip=skip, limit=limit, user_id=current_user.userId)
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

    # Check access permission
    ensure_resource_access(app.userId, current_user)

    # Fetch versions if git_link exists. Skipped for soft-deleted apps
    # — listing versions is a "what could I deploy" affordance and the
    # answer is "nothing", you already deleted this app.
    if app.git_link and app.deleted_at is None:
        try:
            app.versions = git_service.get_versions(app.git_link)
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
@router.get("/{app_id}/variables", response_model=list[dict[str, Any]])
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

    if not app.git_link:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="App has no Git repository configured"
        )

    logger = logging.getLogger(__name__)
    deployment_id = f"vars_{app_id}_{version}".replace("/", "_")
    repo_path = None

    try:
        # Clone repository with sparse checkout (only variable files)
        repo_path = git_service.clone_release_vars(app.git_link, version, deployment_id)

        variables = []

        # Parse Terraform variables
        tf_vars_path = os.path.join(repo_path, "terraform", "variables.tf")
        if os.path.exists(tf_vars_path):
            logger.info(f"Parsing Terraform variables from {tf_vars_path}")
            variables.extend(_parse_terraform_variables(tf_vars_path))

        # Parse Packer variables
        packer_vars_path = os.path.join(repo_path, "packer", "variables.pkr.hcl")
        if os.path.exists(packer_vars_path):
            logger.info(f"Parsing Packer variables from {packer_vars_path}")
            variables.extend(_parse_packer_variables(packer_vars_path))

        if not variables:
            logger.warning(f"No variables found in {repo_path}")

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

    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Failed to get variables for app {app_id} version {version}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch variables",
        )
    finally:
        # Always cleanup cloned repository
        if repo_path:
            try:
                git_service.cleanup_repository(repo_path)
                logger.info(f"Cleaned up repository at {repo_path}")
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup repository: {str(cleanup_error)}")


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
    """
    Update an app
    - **Owner or Teacher/Admin** can update
    """
    app = crud_apps.get_app(db, app_id)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )

    # Check access permission
    ensure_resource_access(app.userId, current_user)

    # Image is set separately because the AppUpdate.image is a
    # data-URL string and update_app excludes it from the bulk
    # ``setattr`` loop. ``image=None`` means "leave unchanged"
    # (Pydantic ``exclude_unset`` semantics) — to actively clear
    # the image, send ``image=""``.
    image_was_provided = "image" in app_update.model_fields_set
    image_bytes, image_mime = (None, None)
    if image_was_provided:
        image_bytes, image_mime = parse_image_data_url(app_update.image)

    updated_app = crud_apps.update_app(db, app_id, app_update)
    if image_was_provided:
        updated_app = crud_apps.set_app_image(db, app_id, image_bytes, image_mime)
    return _serialize_app(updated_app)


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
