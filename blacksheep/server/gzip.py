import asyncio
import gzip
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import Awaitable, Callable, Iterable, List, Optional

from blacksheep import Content, Request, Response
from blacksheep.server.application import Application
from blacksheep.server.normalization import ensure_response


class GzipMiddleware:
    """
    The gzip compression middleware for all requests with a body larger than
    the specified minimum size and with the "gzip" encoding in the "Accept-Encoding"
    header. The middleware runs compression in a separate executor.

    Parameters
    ----------
    min_size: int
        The minimum size of the response body to compress.
    comp_level: int
        The compression level to use.
    handled_types: Optional[Iterable[bytes]]
        The list of content types to compress.
    executor: Executor
        The executor instance to use for compression. If not specified, a
        ThreadPoolExecutor is used. If you specify an executor, you are responsible
        for shutting it down. If you do not specify an executor, the middleware
        will shut it down when it is disposed.
    """

    handled_types: List[bytes] = [
        b"json",
        b"xml",
        b"yaml",
        b"html",
        b"text/plain",
        b"application/javascript",
        b"text/css",
        b"text/csv",
    ]

    def __init__(
        self,
        min_size: int = 500,
        comp_level: int = 5,
        handled_types: Optional[Iterable[bytes]] = None,
        executor: Optional[Executor] = None,
    ):
        self.min_size = min_size
        self.comp_level = comp_level

        self._shutdown_executor = False
        if executor is not None:
            self._executor = executor
        else:
            self._executor = ThreadPoolExecutor()
            self._shutdown_executor = True

        if handled_types is not None:
            self.handled_types = self._normalize_types(handled_types)

    def _normalize_types(self, types: Iterable[bytes]) -> List[bytes]:
        """
        Normalizes the types to bytes.
        """
        normalized_types = []
        for _type in types:
            if isinstance(_type, str):
                normalized_types.append(_type.encode("ascii"))
            else:
                normalized_types.append(_type)
        return normalized_types

    def should_handle(self, request: Request, response: Response) -> bool:
        """
        Returns True if the response should be compressed.
        """

        def _is_handled_type(content_type) -> bool:
            content_type = content_type.lower()
            return any(_type in content_type for _type in self.handled_types)

        def is_handled_encoding() -> bool:
            return request.headers is not None and b"gzip" in (
                request.headers.get_single(b"accept-encoding") or ""
            )

        def is_handled_response_content() -> bool:
            if response is None or response.content is None:
                return False

            body_pass: bool = (
                response.content.body is not None
                and len(response.content.body) > self.min_size
            )

            content_type_pass: bool = (
                response.content.type is not None
                and _is_handled_type(response.content.type)
            )

            return all(
                (
                    body_pass,
                    content_type_pass,
                )
            )

        return all(
            (
                is_handled_encoding(),
                is_handled_response_content(),
            )
        )

    async def __call__(
        self, request: Request, handler: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = ensure_response(await handler(request))

        if response is None or response.content is None:
            return response

        if not self.should_handle(request, response):
            return response

        loop = asyncio.get_running_loop()
        compressed_body = await loop.run_in_executor(
            self._executor,
            gzip.compress,
            response.content.body,
            self.comp_level,
        )

        response.with_content(
            Content(
                content_type=response.content.type,
                data=compressed_body,
            )
        )
        response.add_header(b"content-encoding", b"gzip")
        return response

    def __del__(self):
        if self._shutdown_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)


def use_gzip_compression(
    app: Application,
    handler: Optional[GzipMiddleware] = None,
) -> GzipMiddleware:
    """
    Configures the application to use gzip compression for all responses with gzip
    in accept-encoding header.
    """
    if handler is None:
        handler = GzipMiddleware()

    app.middlewares.append(handler)

    return handler
