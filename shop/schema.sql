-- Drop tables if they exist to ensure a clean slate.
DROP TABLE IF EXISTS parks;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS conversations;
DROP TABLE IF EXISTS messages;

-- Table for products/parks
CREATE TABLE parks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    location TEXT,
    price REAL NOT NULL,
    description TEXT,
    date_added TEXT NOT NULL,
    web_details TEXT,
    type TEXT,
    link1 TEXT,
    link2 TEXT,
    admin_id TEXT NOT NULL,
    home_delivery INTEGER DEFAULT 0,
    image_filenames TEXT DEFAULT '[]',
    views INTEGER DEFAULT 0,
    inquiries INTEGER DEFAULT 0
);

-- Table for users (customers and admins/merchants)
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    number TEXT,
    location TEXT,
    password TEXT NOT NULL,
    photo TEXT,
    role TEXT NOT NULL DEFAULT 'normal',
    reviews TEXT DEFAULT '[]',
    ratings_total INTEGER DEFAULT 0,
    ratings_count INTEGER DEFAULT 0
);

-- Table to define a conversation between two participants
CREATE TABLE conversations (
    id TEXT PRIMARY KEY, -- e.g., "user_id-admin_id"
    deleted_by TEXT NOT NULL DEFAULT '[]'
);

-- NEW: Table to store individual messages
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    text TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0,
    is_system INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (conversation_id) REFERENCES conversations (id)
);