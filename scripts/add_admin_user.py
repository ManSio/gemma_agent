#!/usr/bin/env python3
"""
Script to add an admin user to the Universal Social Assistant database
"""
import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core.database import Base, User
from core.database import DATABASE_URL

def add_admin_user(external_id: str, name: str = "Admin User"):
    """Add an admin user to the database"""
    
    # Create database engine
    engine = create_engine(DATABASE_URL)
    
    # Create session
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    try:
        # Check if user already exists
        existing_user = db.query(User).filter(User.external_id == external_id).first()
        if existing_user:
            print(f"User with ID {external_id} already exists")
            print(f"User details: {existing_user.name} (role: {existing_user.role})")
            if existing_user.role != "admin":
                # Update role to admin if not already admin
                existing_user.role = "admin"
                db.commit()
                db.refresh(existing_user)
                print(f"Updated user {existing_user.name} to admin role")
            else:
                print("User is already an admin")
            return True
        
        # Create admin user
        admin = User(
            external_id=external_id,
            name=name,
            role="admin"
        )
        
        db.add(admin)
        db.commit()
        db.refresh(admin)
        
        print(f"Admin user added successfully!")
        print(f"User ID: {admin.id}")
        print(f"External ID: {admin.external_id}")
        print(f"Name: {admin.name}")
        print(f"Role: {admin.role}")
        print(f"Created: {admin.created_at}")
        
        return True
        
    except Exception as e:
        print(f"Error adding admin user: {e}")
        db.rollback()
        return False
        
    finally:
        db.close()

if __name__ == "__main__":
    # Get Telegram ID from environment or command line
    telegram_id = os.getenv("TELEGRAM_ID", None)
    
    if not telegram_id:
        print("Set TELEGRAM_ID (your numeric user id from @userinfobot).")
        sys.exit(1)
    
    # Add the admin user
    success = add_admin_user(telegram_id, "System Administrator")
    
    if success:
        print("Admin user setup completed successfully!")
        print(f"You can now use your Telegram ID {telegram_id} to access admin features")
    else:
        print("Failed to add admin user")
        sys.exit(1)