CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    role VARCHAR(50) NOT NULL DEFAULT 'user',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_role ON users (role);

-- Example seed user for quick testing.
INSERT INTO users (email, password_hash, first_name, last_name, role)
VALUES (
    'admin@homelab.local',
    '$2b$12$8Cj5XkVJ8GNr9XELmD7.uO0mIUSrM2iKzQx8D8BJP4h3w8mYhN8rC',
    'Admin',
    'User',
    'admin'
)
ON CONFLICT (email) DO NOTHING;
