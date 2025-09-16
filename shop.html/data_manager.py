import json
import os
import sqlite3
from datetime import datetime, timezone

DATABASE_PATH = 'database.db'

def get_db():
    """Opens a new database connection."""
    db = sqlite3.connect(DATABASE_PATH)
    db.row_factory = sqlite3.Row # This allows accessing columns by name
    return db

def get_all_parks():
    """Reads all parks from the SQLite database."""
    print(f"Fetching all parks from {DATABASE_PATH}")
    db = get_db()
    parks = db.execute('SELECT * FROM parks ORDER BY date_added DESC').fetchall()
    db.close()
    
    # Convert Row objects to dictionaries
    return [dict(park) for park in parks]

def add_park(park_data, image_extensions, admin_id):
    """Adds a new park to the database."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO parks (name, location, price, description, date_added, web_details, type, link1, link2, admin_id, home_delivery, image_filenames)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            park_data.get('name'), park_data.get('location'), park_data.get('price'),
            park_data.get('description'), datetime.now(timezone.utc).isoformat(),
            park_data.get('web_details'), park_data.get('type'),
            park_data.get('link1'), park_data.get('link2'), admin_id,
            1 if park_data.get('home_delivery', False) else 0,
            '[]' # Placeholder, will be updated next
        )
    )
    new_id = cursor.lastrowid
    # Now generate filenames with the final ID
    filenames = [f"{new_id}_{i+1}.{ext}" for i, ext in enumerate(image_extensions)]
    cursor.execute('UPDATE parks SET image_filenames = ? WHERE id = ?', (json.dumps(filenames), new_id))
    # Instead of re-fetching, construct the new park object directly to avoid race conditions.
    new_park_data = {
        'id': new_id,
        'name': park_data.get('name'),
        'location': park_data.get('location'),
        'price': park_data.get('price'),
        'description': park_data.get('description'),
        'date_added': datetime.now(timezone.utc).isoformat(),
        'web_details': park_data.get('web_details'),
        'type': park_data.get('type'),
        'link1': park_data.get('link1'),
        'link2': park_data.get('link2'),
        'admin_id': admin_id,
        'home_delivery': 1 if park_data.get('home_delivery', False) else 0,
        'image_filenames': filenames # Return a Python list for immediate use in app.py
    }
    db.commit()
    db.close()
    return new_park_data

def get_park_by_id(park_id):
    """Finds a single park by its ID."""
    db = get_db()
    park = db.execute('SELECT * FROM parks WHERE id = ?', (park_id,)).fetchone()
    db.close()
    return dict(park) if park else None

def update_park(park_id, update_data, new_image_extensions=None):
    """Updates an existing park's details."""
    park_to_update = get_park_by_id(park_id)
    if not park_to_update:
        return None, None

    old_image_filenames_json = park_to_update.get('image_filenames', '[]')
    try:
        old_image_filenames = json.loads(old_image_filenames_json)
    except (json.JSONDecodeError, TypeError): old_image_filenames = []
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        UPDATE parks SET
            name = ?, location = ?, price = ?, description = ?, web_details = ?,
            type = ?, link1 = ?, link2 = ?, home_delivery = ?, image_filenames = ?
        WHERE id = ?
        """,
        (
            update_data.get('name', park_to_update['name']),
            update_data.get('location', park_to_update['location']),
            update_data.get('price', park_to_update['price']),
            update_data.get('description', park_to_update['description']),
            update_data.get('web_details', park_to_update['web_details']),
            update_data.get('type', park_to_update['type']),
            update_data.get('link1', park_to_update['link1']),
            update_data.get('link2', park_to_update['link2']),
            1 if update_data.get('home_delivery', False) else 0,
            json.dumps(update_data.get('image_filenames', old_image_filenames)),
            park_id
        )
    )
    db.commit()
    db.close()

    updated_park = get_park_by_id(park_id)
    # The logic for which files to delete is now in app.py, so we just return the old list
    return updated_park, old_image_filenames

def delete_park(park_id):
    """Deletes a park from the database."""
    park_to_delete = get_park_by_id(park_id)
    if park_to_delete:
        db = get_db()
        db.execute('DELETE FROM parks WHERE id = ?', (park_id,))
        db.commit()
        db.close()
    return park_to_delete

def increment_product_view(product_id):
    """Increments the view count for a specific product."""
    db = get_db()
    db.execute('UPDATE parks SET views = views + 1 WHERE id = ?', (product_id,))
    db.commit()
    db.close()

def increment_product_inquiry(product_id):
    """Increments the inquiry count for a specific product."""
    db = get_db()
    db.execute('UPDATE parks SET inquiries = inquiries + 1 WHERE id = ?', (product_id,))
    db.commit()
    db.close()