"""Explicit, local authentication-provider boundary; no built-in remote service."""

import asyncio
import base64
import json
import sys

from .wire import Message, ProtocolError, parameter


class AuthenticationProvider:
    """JSON-lines subprocess: certificate or sign; private keys stay in the helper."""

    def __init__(self, executable):
        self.executable = executable
        self.process = None

    def _argv(self):
        """Command used to launch the helper.

        A ``.py`` helper is launched with the current interpreter
        (``sys.executable``) instead of being executed directly.
        Direct execution needs the +x bit, which a fresh ``git
        clone`` does not guarantee -- the GitHub snapshot push that
        created this repo stores files as 0644, so a cloned
        ``tools/local_mfi_v3_helper.py`` failed with
        ``[Errno 13] Permission denied`` even though the file itself
        was fine. Running it via the interpreter makes the helper
        work regardless of the file mode, and uses the same venv
        interpreter (with cryptography installed) as the server.
        """
        if str(self.executable).endswith(".py"):
            return [sys.executable, str(self.executable)]
        return [str(self.executable)]

    async def request(self, operation, **fields):
        if self.process is None:
            try:
                self.process = await asyncio.create_subprocess_exec(
                    *self._argv(),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    limit=128 * 1024,
                )
            except PermissionError:
                # Non-.py helper without +x (e.g. after a fresh clone):
                # retry through the interpreter rather than failing the
                # whole session.
                if self._argv()[0] == sys.executable:
                    raise
                self.process = await asyncio.create_subprocess_exec(
                    sys.executable, str(self.executable),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    limit=128 * 1024,
                )
        self.process.stdin.write(json.dumps({"op": operation, **fields}).encode() + b"\n")
        await self.process.stdin.drain()
        response = await asyncio.wait_for(self.process.stdout.readline(), 40)
        if not response:
            raise RuntimeError("Authentication provider exited without a response")
        result = json.loads(response)
        if result.get("error"):
            raise RuntimeError(f"Authentication provider: {result['error']}")
        return result

    async def certificate(self):
        result = await self.request("certificate")
        certificate = base64.b64decode(result["certificate"], validate=True)
        if not certificate:
            raise ProtocolError("Empty authentication certificate")
        body = parameter(0, certificate)
        if result.get("type") == "baa":
            intermediate = base64.b64decode(result["intermediate"], validate=True)
            if not intermediate:
                raise ProtocolError("Empty BAA intermediate certificate")
            body += parameter(1, b"\x01") + parameter(2, intermediate)
        elif result.get("type") != "mfi":
            raise ProtocolError("Provider type must be baa or mfi")
        return Message(0xAA01, body)

    async def sign(self, challenge):
        if not 1 <= len(challenge) <= 4096:
            raise ProtocolError("Invalid authentication challenge size")
        result = await self.request("sign", challenge=base64.b64encode(challenge).decode("ascii"))
        signature = base64.b64decode(result["signature"], validate=True)
        if not 1 <= len(signature) <= 4096:
            raise ProtocolError("Invalid authentication signature size")
        return Message(0xAA03, parameter(0, signature))

    async def close(self):
        if self.process is not None:
            if self.process.stdin:
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except TimeoutError:
                self.process.terminate()
                await self.process.wait()
