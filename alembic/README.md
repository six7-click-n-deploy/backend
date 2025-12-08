# Alembic Database Migrations

Dieses Verzeichnis enthält die Alembic-Migrationen für die Datenbank.

## 📁 Struktur

```
alembic/
├── env.py                    # Alembic Environment Configuration
├── script.py.mako           # Migration Template
└── versions/                # Migration Scripts
    └── .gitkeep
```

## 🚀 Verwendung

### Erste Migration erstellen

Nach der Initialisierung müssen Sie die erste Migration erstellen:

```bash
# Automatisch basierend auf Models generieren
alembic revision --autogenerate -m "Initial migration"

# Oder manuell eine leere Migration erstellen
alembic revision -m "Initial migration"
```

### Migration anwenden

```bash
# Alle ausstehenden Migrationen anwenden
alembic upgrade head

# Eine bestimmte Revision anwenden
alembic upgrade <revision_id>

# Eine Revision zurückrollen
alembic downgrade -1

# Zu einer bestimmten Revision zurück
alembic downgrade <revision_id>
```

### Migration-Historie

```bash
# Aktuelle Revision anzeigen
alembic current

# Alle Revisionen anzeigen
alembic history

# Detaillierte Historie
alembic history --verbose
```

### Neue Migration erstellen

Wenn Sie Änderungen an den Models vorgenommen haben:

```bash
# Automatisch Migration generieren
alembic revision --autogenerate -m "Add new column to users table"

# Manuelle Migration erstellen
alembic revision -m "Custom migration"
```

## ⚙️ Konfiguration

Die Konfiguration erfolgt in:
- `alembic.ini` - Alembic Hauptkonfiguration
- `alembic/env.py` - Environment Setup (verwendet DATABASE_URL aus config.py)

## 🔧 Best Practices

1. **Immer Review**: Überprüfen Sie autogenerierte Migrationen vor dem Commit
2. **Beschreibende Namen**: Verwenden Sie aussagekräftige Migration-Namen
3. **Testen**: Testen Sie Upgrade und Downgrade
4. **Version Control**: Committen Sie Migrationen ins Git
5. **Backup**: Erstellen Sie vor Produktions-Migrationen ein Datenbank-Backup

## 📝 Beispiel Migration

```python
"""Add is_active column to users

Revision ID: abc123
Revises: def456
Create Date: 2025-12-08 10:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'abc123'
down_revision = 'def456'

def upgrade() -> None:
    op.add_column('users', sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'))

def downgrade() -> None:
    op.drop_column('users', 'is_active')
```

## 🐳 Docker Integration

Im Docker Container:

```bash
# Migration ausführen
docker compose exec backend alembic upgrade head

# Migration erstellen
docker compose exec backend alembic revision --autogenerate -m "Migration name"
```

## 🔍 Troubleshooting

### "Target database is not up to date"
```bash
alembic stamp head
```

### Migration zurücksetzen
```bash
alembic downgrade base  # Alle Migrationen zurückrollen
```

### Datenbank neu aufbauen
```bash
alembic downgrade base
alembic upgrade head
```
