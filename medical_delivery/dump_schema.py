from app import create_app, db
import app.models  # ensure models are imported and metadata populated
from sqlalchemy.schema import CreateTable

app = create_app()
with app.app_context():
    meta = db.metadata
    with open('app/templates/sql schema.txt', 'w') as f:
        for table in meta.sorted_tables:
            ddl = str(CreateTable(table).compile(db.engine))
            f.write(ddl + "\n\n")
    print("Schema written to app/templates/sql schema.txt")
