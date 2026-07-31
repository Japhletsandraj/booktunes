from app.core.database import get_db
from app.models.models import Book

def check_database():
    db = next(get_db())
    
    # Check tables
    from sqlalchemy import inspect
    inspector = inspect(db.bind)
    tables = inspector.get_table_names()
    print(f"📋 Tables: {', '.join(tables)}")
    
    # Check book count
    count = db.query(Book).count()
    print(f"📚 Books: {count}")
    
    # Check user count
    from app.models.models import User
    user_count = db.query(User).count()
    print(f"👤 Users: {user_count}")

if __name__ == "__main__":
    check_database()