"""
Encryption utilities for sensitive data (credentials)
"""
from cryptography.fernet import Fernet
from app.core.config import settings


class EncryptionService:
    """Service for encrypting and decrypting sensitive data"""

    def __init__(self):
        """Initialize the encryption service with the configured key"""
        self.cipher = Fernet(settings.ENCRYPTION_KEY.encode())

    def encrypt(self, plain_text: str) -> str:
        """
        Encrypt a plain text string

        Args:
            plain_text: Text to encrypt

        Returns:
            str: Encrypted text (base64 encoded)
        """
        if not plain_text:
            return ""

        encrypted_bytes = self.cipher.encrypt(plain_text.encode())
        return encrypted_bytes.decode()

    def decrypt(self, encrypted_text: str) -> str:
        """
        Decrypt an encrypted string

        Args:
            encrypted_text: Encrypted text (base64 encoded)

        Returns:
            str: Decrypted plain text

        Raises:
            cryptography.fernet.InvalidToken: If decryption fails
        """
        if not encrypted_text:
            return ""

        decrypted_bytes = self.cipher.decrypt(encrypted_text.encode())
        return decrypted_bytes.decode()


# Global encryption service instance
encryption_service = EncryptionService()
