import keyring


SERVICE = "IBKR Lot Tracker"
ACCOUNT = "ibkr-flex-token"


class CredentialStore:
    def __init__(self, backend=keyring):
        self._backend = backend

    def get_token(self) -> str:
        return self._backend.get_password(SERVICE, ACCOUNT) or ""

    def has_token(self) -> bool:
        return bool(self.get_token())

    def set_token(self, token: str) -> None:
        if not token:
            raise ValueError("Flex token must not be empty")
        self._backend.set_password(SERVICE, ACCOUNT, token)

    def clear_token(self) -> None:
        try:
            self._backend.delete_password(SERVICE, ACCOUNT)
        except keyring.errors.PasswordDeleteError:
            pass
