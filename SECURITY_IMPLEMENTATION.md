# Security Implementation Details

## Overview

The security layer implements comprehensive protection for user data, including encryption, role-based access control, and audit logging. This implementation ensures that sensitive information is properly protected while maintaining system functionality.

## Encryption Mechanism

### AES-256 Implementation
All sensitive data is encrypted using AES-256 encryption. The security layer uses the `cryptography` library:

```python
from cryptography.fernet import Fernet

# Key must be 32 URL-safe base64-encoded bytes
encryption_key = "your_32_byte_key_here============"
fernet = Fernet(encryption_key.encode())
```

### Encrypted Fields
The following fields are automatically encrypted:
- Psychology profile data
- Digital twin information  
- Parent-child relationship data
- User profile information
- Progress data
- Schedule information
- Any other sensitive user information

### Key Management
Encryption keys are stored as environment variables and never in source code. These values must be set in the `.env.example` file:

```
SECURITY_AES_KEY=your_32_byte_security_key_here
SECURITY_SALT=your_salt_here
```

- `SECURITY_AES_KEY`: 32-byte key for AES-256 encryption (must be exactly 32 bytes)
- `SECURITY_SALT`: Salt value used for key derivation (recommended length: 16-32 bytes)

These values should be generated using a secure random generator and kept private.

Note: The `ENCRYPTION_KEY` variable is used for local data encryption, while `SECURITY_AES_KEY` is used for the security layer's encryption operations. While they may be the same in development environments, production deployments should use different, randomly generated keys for each purpose.

## Role-Based Access Control (RBAC)

### Roles Defined
The system implements five user roles with different permissions:

1. **Child** - Limited access, primarily for learning data
2. **Parent** - Access to child data and family information
3. **Teacher** - Educational access and student data
4. **Admin** - Full system access and management
5. **System** - Internal system operations

### Permission Matrix
Each role has defined permissions for resources:

**Child:**
- Read: user_profile, schedule, progress
- Write: schedule, progress

**Parent:**
- Read: user_profile, schedule, progress, psychology, digital_twin
- Write: schedule, progress

**Teacher:**  
- Read: user_profile, schedule, progress, psychology, digital_twin
- Write: schedule, progress, psychology, digital_twin

**Admin:**
- Read: user_profile, schedule, progress, psychology, digital_twin, settings
- Write: user_profile, schedule, progress, psychology, digital_twin, settings

## Security Policy Configuration

The security policy is defined in `core/security.py` as a configurable dictionary. This allows for easy modification of access rules:

```python
# Example policy structure
{
    "roles": {
        "child": {
            "read": ["user_profile", "schedule", "progress"],
            "write": ["schedule", "progress"],
            "admin": []
        },
        # ... other roles
    },
    "resources": {
        "user_profile": ["child", "parent", "teacher", "admin"],
        "psychology": ["parent", "teacher", "admin"],
        # ... other resources
    }
}
```

## Access Logging

All access attempts are logged with:
- User ID
- Resource accessed
- Action performed (read/write/admin)
- Success status
- Timestamp
- Additional details

These logs help with auditing and detecting potential security issues.

## Usage in Modules

### Encryption
```python
from core.security import security_layer

# Encrypt sensitive data
encrypted_data = security_layer.encrypt_data("sensitive content")

# Decrypt when needed
decrypted_data = security_layer.decrypt_data(encrypted_data)
```

### Permission Checking
```python
from core.security import security_layer

# Check if user has permission
has_permission = security_layer.check_permission(user, "read", "psychology")
```

### Access Logging
```python
# Log access attempts
security_layer.log_access(
    user_id=user.id,
    resource="psychology",
    action="read",
    success=True,
    details="Child viewed psychology profile"
)
```

## Error Handling

The security layer implements comprehensive error handling for:
- Invalid keys or encryption failures
- Permission violations  
- Access logging failures
- Policy evaluation errors

## Security Best Practices

1. **Never store keys in source code** - All keys in environment variables
2. **Encrypt sensitive data at rest** - All personal information encrypted
3. **Role-based permissions** - Principle of least privilege
4. **Audit logging** - Comprehensive access tracking
5. **Environment isolation** - Different keys per environment
6. **Regular key rotation** - Security best practice support

This security implementation protects user privacy while maintaining all system functionality.