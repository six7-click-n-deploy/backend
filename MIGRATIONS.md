# Database Migrations Guide

## 🚀 Schnellstart

### 1. Installation der Dependencies

```bash
# Mit pip
pip install -e ".[dev]"

# Oder direkt
pip install alembic
```

### 2. Erste Migration erstellen

```bash
# Automatisch aus den Models generieren
alembic revision --autogenerate -m "Initial migration with users, git_repos and tasks tables"
```

### 3. Migration anwenden

```bash
# Datenbank-Schema erstellen
alembic upgrade head
```

### 4. Prüfen ob es funktioniert hat

```bash
# Aktuelle Revision anzeigen
alembic current

# Historie anzeigen
alembic history
```

## 🔄 Workflow für Schema-Änderungen

### Beispiel: Neue Spalte zu User-Tabelle hinzufügen

**1. Model anpassen** (`models.py`):
```python
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    username = Column(String, unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)  # NEU
    created_at = Column(DateTime, default=datetime.utcnow)
```

**2. Migration generieren**:
```bash
alembic revision --autogenerate -m "Add is_active column to users table"
```

**3. Generierte Migration prüfen** (`alembic/versions/xxx_add_is_active.py`):
```python
def upgrade() -> None:
    op.add_column('users', sa.Column('is_active', sa.Boolean(), nullable=True))

def downgrade() -> None:
    op.drop_column('users', 'is_active')
```

**4. Migration anwenden**:
```bash
alembic upgrade head
```

**5. Bei Bedarf zurückrollen**:
```bash
alembic downgrade -1
```

## 📋 Häufige Befehle

```bash
# Neue Migration erstellen (autogenerate)
alembic revision --autogenerate -m "Description"

# Neue leere Migration erstellen
alembic revision -m "Description"

# Alle Migrationen anwenden
alembic upgrade head

# Eine Revision zurück
alembic downgrade -1

# Zu spezifischer Revision
alembic upgrade <revision_id>
alembic downgrade <revision_id>

# Alle Migrationen zurückrollen
alembic downgrade base

# Aktuelle Revision
alembic current

# Migrations-Historie
alembic history

# Detaillierte Historie
alembic history --verbose

# Migration als SQL ausgeben (ohne ausführen)
alembic upgrade head --sql
```

## 🐳 Docker/Compose Integration

### In docker-compose.yml

```yaml
services:
  backend:
    build: ./backend
    command: >
      sh -c "alembic upgrade head && 
             uvicorn main:app --host 0.0.0.0 --port 8000"
    # ...
```

### Manuell im Container

```bash
# Migration im Container ausführen
docker compose exec backend alembic upgrade head

# Migration im Container erstellen
docker compose exec backend alembic revision --autogenerate -m "New migration"

# Status prüfen
docker compose exec backend alembic current
```

## ⚠️ Best Practices

### ✅ DO:
- Immer autogenerierte Migrationen reviewen
- Beschreibende Migration-Namen verwenden
- Upgrade und Downgrade testen
- Migrationen in Version Control committen
- Backup vor Produktions-Migrationen

### ❌ DON'T:
- Migrationen nach dem Merge nicht mehr ändern
- Keine manuellen Schema-Änderungen an der DB
- Keine Daten-Migrationen ohne Downgrade
- Nicht mehrere Schema-Änderungen in einer Migration mischen

## 🔧 Troubleshooting

### Problem: "Target database is not up to date"
```bash
# Datenbank als "up to date" markieren
alembic stamp head
```

### Problem: Migration schlägt fehl
```bash
# Eine Revision zurück
alembic downgrade -1

# Migration-File anpassen
# Erneut versuchen
alembic upgrade head
```

### Problem: Datenbank komplett neu aufbauen
```bash
# Alle Migrationen zurückrollen
alembic downgrade base

# Oder: Datenbank droppen und neu erstellen
# (PostgreSQL Beispiel)
dropdb backend_db
createdb backend_db

# Dann alle Migrationen neu anwenden
alembic upgrade head
```

### Problem: Merge-Konflikt bei Migrationen
```bash
# Heads anzeigen
alembic heads

# Merge-Migration erstellen
alembic merge -m "Merge migrations" <rev1> <rev2>
```

## 📚 Weitere Ressourcen

- [Alembic Dokumentation](https://alembic.sqlalchemy.org/)
- [SQLAlchemy Dokumentation](https://docs.sqlalchemy.org/)
- [Alembic Tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
