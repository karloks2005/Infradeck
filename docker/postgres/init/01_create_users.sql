CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'user',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_role ON users (role);

-- Example seed user for quick testing.
INSERT INTO users (email, password_hash, name, role)
VALUES (
    'admin@example.com',
    '$2b$12$pb1MBMmCHsJOwpZ4k7aVoOpOJfGDS/TBmR8KwvibUv15z4tgYAm3O',
    'Admin User',
    'admin'
)
ON CONFLICT (email) DO NOTHING;
