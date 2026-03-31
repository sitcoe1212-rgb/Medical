from app import create_app, db
from sqlalchemy import inspect

app = create_app()
with app.app_context():
    insp = inspect(db.engine)
    for tbl in ['orders','prescriptions','stores','payments','notifications','users']:
        print(tbl, insp.get_foreign_keys(tbl))
