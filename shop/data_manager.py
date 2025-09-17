import json
import os
from datetime import datetime, timezone

# The database file is located in the 'static' directory, which is standard
# for serving assets like JSON files and images.
DATABASE_PATH = os.path.join('static', 'products.json')

def _get_next_id(items):
    """Helper function to get the next available ID as a zero-padded string."""
    if not items:
        return "000001"
    # Convert string IDs to integers for comparison
    max_id = max(int(item.get('id', '0')) for item in items)
    next_id_num = max_id + 1
    return f"{next_id_num:06d}" # Formats as 6-digit string with leading zeros

def get_all_parks():
    """Reads all parks from the JSON database file."""
    if not os.path.exists(DATABASE_PATH):
        return []
    try:
        with open(DATABASE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        # Return an empty list if the file is empty, not found, or corrupted
        return []

def _save_all_parks(parks):
    """Saves a list of parks to the JSON database file."""
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    with open(DATABASE_PATH, 'w', encoding='utf-8') as f:
        json.dump(parks, f, indent=2, ensure_ascii=False)

def add_park(park_data, image_extensions, admin_id):
    """Adds a new park to the database, generating the ID and multiple filenames."""
    parks = get_all_parks()
    new_id = _get_next_id(parks)
    
    # Generate a list of filenames, one for each uploaded image extension
    filenames = [f"{new_id}_{i+1}.{ext}" for i, ext in enumerate(image_extensions)]
    
    new_park = {
        'id': new_id,
        'name': park_data.get('name'),
        'location': park_data.get('location'),
        'price': park_data.get('price'),
        'description': park_data.get('description'),
        'date_added': datetime.now(timezone.utc).isoformat(), # Automatically add timestamp
        'web_details': park_data.get('web_details'),
        'type': park_data.get('type'),
        'image_filenames': filenames, # Store a list of filenames
        'link1': park_data.get('link1'), # Added link1
        'link2': park_data.get('link2'),  # Added link2
        'admin_id': admin_id,
        'views': 0,
        'inquiries': 0,
        'home_delivery': park_data.get('home_delivery', False) # Add the home_delivery field
    }
    parks.append(new_park)
    _save_all_parks(parks)
    return new_park

def get_park_by_id(park_id):
    """Finds a single park by its string ID."""
    parks = get_all_parks()
    for park in parks:
        if park.get('id') == park_id:
            return park
    return None # Return None if no park is found

def update_park(park_id, update_data, new_image_extensions=None):
    """Updates an existing park's details and optionally its image filename."""
    parks = get_all_parks()
    park_to_update = None
    for park in parks:
        if park.get('id') == park_id:
            park_to_update = park
            break
    
    if not park_to_update:
        return None, None
    
    # Get the list of old filenames to be returned for deletion
    old_image_filenames = park_to_update.get('image_filenames', [])
    
    # Update fields from the provided data
    for key, value in update_data.items():
        if key in park_to_update:
            park_to_update[key] = value
    
    # Explicitly handle the home_delivery checkbox, as it might be a new key
    park_to_update['home_delivery'] = update_data.get('home_delivery', False)

    # If new images are being uploaded, replace the list of filenames
    if new_image_extensions:
        park_to_update['image_filenames'] = [f"{park_id}_{i+1}.{ext}" for i, ext in enumerate(new_image_extensions)]
    
    _save_all_parks(parks)
    return park_to_update, old_image_filenames

def delete_park(park_id):
    """Deletes a park from the database and returns the deleted park data."""
    parks = get_all_parks()
    park_to_delete = None
    for park in parks:
        if park.get('id') == park_id:
            park_to_delete = park
            break
    
    if park_to_delete:
        parks.remove(park_to_delete)
        _save_all_parks(parks)
        return park_to_delete
    return None # Return None if the park was not found

def increment_product_view(product_id):
    """Increments the view count for a specific product."""
    parks = get_all_parks()
    product_found = False
    for park in parks:
        if park.get('id') == product_id:
            park['views'] = park.get('views', 0) + 1
            product_found = True
            break
    if product_found:
        _save_all_parks(parks)

def increment_product_inquiry(product_id):
    """Increments the inquiry count for a specific product."""
    parks = get_all_parks()
    product_found = False
    for park in parks:
        if park.get('id') == product_id:
            park['inquiries'] = park.get('inquiries', 0) + 1
            product_found = True
            break
    if product_found:
        _save_all_parks(parks)