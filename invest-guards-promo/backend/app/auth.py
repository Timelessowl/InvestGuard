"""Single-account HTTP Basic authentication, shared by the API and Nginx."""
import os
import secrets
from dataclasses import dataclass, field

from fastapi import HTTPException, Request
from fastapi.security import HTTPBasic

basic = HTTPBasic(realm='InvestSignal')


@dataclass(frozen=True)
class AuthSettings:
    username: str
    password: str = field(repr=False)

    @classmethod
    def from_env(cls):
        username = os.environ.get('AUTH_USERNAME', 'poc_user')
        password = os.environ.get('AUTH_PASSWORD', '')
        if not username.strip() or ':' in username or not username.isascii():
            raise RuntimeError('AUTH_USERNAME must be a non-empty ASCII username without colons.')
        if not password.strip() or not password.isascii():
            raise RuntimeError('AUTH_PASSWORD must be a non-empty ASCII password.')
        return cls(username, password)

    async def verify(self, request: Request):
        credentials = await basic(request)
        username_ok = secrets.compare_digest(credentials.username.encode(), self.username.encode())
        password_ok = secrets.compare_digest(credentials.password.encode(), self.password.encode())
        if not (username_ok and password_ok):
            raise HTTPException(401, 'Invalid credentials', headers={'WWW-Authenticate': 'Basic realm="InvestSignal"'})
