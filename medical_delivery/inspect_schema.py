from app import create_app, db
from sqlalchemy import inspect

app = create_app()
with app.app_context():
    insp = inspect(db.engine)
    print('stores cols:', [c['name'] for c in insp.get_columns('stores')])
    print('users cols:', [c['name'] for c in insp.get_columns('users')])
