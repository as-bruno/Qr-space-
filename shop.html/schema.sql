-- Drop tables if they exist to ensure a clean slate.
DROP TABLE IF EXISTS parks;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS conversations;

-- Parks (Products) Table
CREATE TABLE parks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    location TEXT,
    price REAL,
    description TEXT,
    date_added TEXT NOT NULL,
    web_details TEXT,
    type TEXT,
    image_filenames TEXT, -- Stored as a JSON string '["file1.jpg", "file2.png"]'
    link1 TEXT,
    link2 TEXT,
    admin_id TEXT NOT NULL,
    views INTEGER DEFAULT 0,
    inquiries INTEGER DEFAULT 0,
    home_delivery INTEGER DEFAULT 0 -- 0 for false, 1 for true
);

-- Users Table
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    number TEXT,
    location TEXT,
    password TEXT NOT NULL,
    photo TEXT,
    role TEXT NOT NULL DEFAULT 'normal',
    ratings_total INTEGER DEFAULT 0,
    ratings_count INTEGER DEFAULT 0,
    reviews TEXT -- Stored as a JSON string
);

-- Conversations Table
CREATE TABLE conversations (
    id TEXT PRIMARY KEY, -- e.g., 'user_id-admin_id'
    messages TEXT, -- Stored as a JSON string of message objects
    deleted_by TEXT -- Stored as a JSON string of user IDs
);