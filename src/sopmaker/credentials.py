"""Bring-your-own-key credentials. Never store a key in the workspace or exports."""

import os

SERVICE = "org.sopmaker.desktop"
ACCOUNT = "openai-api-key"


class Credentials:
    def __init__(self, use_environment: bool = True):
        self._key = os.environ.get("OPENAI_API_KEY", "") if use_environment else ""
        self.origin = "environment" if self._key else "none"

    @property
    def key(self) -> str:
        return self._key

    def load_saved(self) -> bool:
        import keyring

        try:
            key = keyring.get_password(SERVICE, ACCOUNT)
        except Exception:
            raise ValueError("The OS credential store is unavailable. Use a session-only key.") from None
        if key:
            self._key, self.origin = key, "credential_store"
        return bool(key)

    def set(self, key: str, remember: bool = False) -> None:
        if not isinstance(key, str) or not key.strip() or len(key) > 1000 or any(c.isspace() for c in key):
            raise ValueError("Enter an API key without spaces or line breaks.")
        if remember:
            import keyring

            try:
                backend = keyring.get_keyring()
                # Refuse third-party plaintext/fallback stores, even if configured by the host.
                module = type(backend).__module__
                if module not in ("keyring.backends.Windows", "keyring.backends.macOS"):
                    raise ValueError("Unsupported credential backend")
                keyring.set_password(SERVICE, ACCOUNT, key.strip())
            except Exception:
                raise ValueError("Could not save to the OS credential store. Use session-only instead.") from None
        self._key = key.strip()
        self.origin = "credential_store" if remember else "session"

    def forget(self) -> None:
        import keyring

        try:
            keyring.delete_password(SERVICE, ACCOUNT)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception:
            raise ValueError("Could not remove the saved credential.") from None
        self._key, self.origin = "", "none"

    def clear_session(self) -> None:
        self._key, self.origin = "", "none"
