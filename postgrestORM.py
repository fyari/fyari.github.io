# Python standard libraries
from __future__ import annotations
import asyncio
import json
from json import JSONDecodeError
from abc import ABC, abstractmethod
from enum import StrEnum
from re import search
import sys
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
    cast,
)
#from typing import Dict, Union, cast


# Third-party libraries
from httpx import BasicAuth, Headers, QueryParams, Timeout, AsyncClient, Response as RequestResponse
from pydantic import BaseModel, ValidationError

from httpx import Client as httpxBaseClient

# Handle the special case of field_validator which has different import paths based on pydantic version
try:
    from pydantic import field_validator  # >= 2.0.0
except ImportError:
    from pydantic import validator as field_validator  # < 2.0.0

# Relative imports

DEFAULT_POSTGREST_CLIENT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}

DEFAULT_POSTGREST_CLIENT_TIMEOUT = 5

#from ..exceptions import APIError, generate_default_error_message
#from ..types import ReturnMethod
#from ..utils import AsyncClient
#from .request_builder import AsyncFilterRequestBuilder, AsyncRequestBuilder
#from .types import CountMethod, Filters, RequestMethod, ReturnMethod
#from .utils import AsyncClient, SyncClient, sanitize_param



class SyncClient(httpxBaseClient):
    def aclose(self) -> None:
        self.close()


def sanitize_param(param: Any) -> str:
    param_str = str(param)
    reserved_chars = ",:()"
    if any(char in param_str for char in reserved_chars):
        return f'"{param_str}"'
    return param_str


def sanitize_pattern_param(pattern: str) -> str:
    return sanitize_param(pattern.replace("%", "*"))


class CountMethod(StrEnum):
    exact = "exact"
    planned = "planned"
    estimated = "estimated"


class Filters(StrEnum):
    NOT = "not"
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IS = "is"
    LIKE = "like"
    ILIKE = "ilike"
    FTS = "fts"
    PLFTS = "plfts"
    PHFTS = "phfts"
    WFTS = "wfts"
    IN = "in"
    CS = "cs"
    CD = "cd"
    OV = "ov"
    SL = "sl"
    SR = "sr"
    NXL = "nxl"
    NXR = "nxr"
    ADJ = "adj"


class RequestMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PATCH = "PATCH"
    PUT = "PUT"
    DELETE = "DELETE"
    HEAD = "HEAD"


class ReturnMethod(StrEnum):
    minimal = "minimal"
    representation = "representation"


class APIError(Exception):
    """
    Base exception for all API errors.
    """

    _raw_error: Dict[str, str]
    message: Optional[str]
    """The error message."""
    code: Optional[str]
    """The error code."""
    hint: Optional[str]
    """The error hint."""
    details: Optional[str]
    """The error details."""

    def __init__(self, error: Dict[str, str]) -> None:
        self._raw_error = error
        self.message = error.get("message")
        self.code = error.get("code")
        self.hint = error.get("hint")
        self.details = error.get("details")
        Exception.__init__(self, str(self))

    def __repr__(self) -> str:
        error_text = f"Error {self.code}:" if self.code else ""
        message_text = f"\nMessage: {self.message}" if self.message else ""
        hint_text = f"\nHint: {self.hint}" if self.hint else ""
        details_text = f"\nDetails: {self.details}" if self.details else ""
        complete_error_text = f"{error_text}{message_text}{hint_text}{details_text}"
        return complete_error_text or "Empty error"

    def json(self) -> Dict[str, str]:
        """Convert the error into a dictionary.

        Returns:
            :class:`dict`
        """
        return self._raw_error


def generate_default_error_message(r):
    return {
        "message": "JSON could not be generated",
        "code": r.status_code,
        "hint": "Refer to full message for details",
        "details": str(r.content),
    }



class BasePostgrestClient(ABC):
    """Base PostgREST client."""

    def __init__(
        self,
        base_url: str,
        *,
        schema: str,
        headers: Dict[str, str],
        timeout: Union[int, float, Timeout],
    ) -> None:
        headers = {
            **headers,
            "Accept-Profile": schema,
            "Content-Profile": schema,
        }
        self.session = self.create_session(base_url, headers, timeout)

    @abstractmethod
    def create_session(
        self,
        base_url: str,
        headers: Dict[str, str],
        timeout: Union[int, float, Timeout],
    ) -> Union[SyncClient, AsyncClient]:
        raise NotImplementedError()

    def auth(
        self,
        token: Optional[str],
        *,
        username: Union[str, bytes, None] = None,
        password: Union[str, bytes] = "",
    ):
        """
        Authenticate the client with either bearer token or basic authentication.

        Raises:
            `ValueError`: If neither authentication scheme is provided.

        .. note::
            Bearer token is preferred if both ones are provided.
        """
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        elif username:
            self.session.auth = BasicAuth(username, password)
        else:
            raise ValueError(
                "Neither bearer token or basic authentication scheme is provided"
            )
        return self

    def schema(self, schema: str):
        """Switch to another schema."""
        self.session.headers.update(
            {
                "Accept-Profile": schema,
                "Content-Profile": schema,
            }
        )
        return self




class QueryArgs(NamedTuple):
    # groups the method, json, headers and params for a query in a single object
    method: RequestMethod
    params: QueryParams
    headers: Headers
    json: Dict[Any, Any]


def pre_select(
    *columns: str,
    count: Optional[CountMethod] = None,
) -> QueryArgs:
    if columns:
        method = RequestMethod.GET
        params = QueryParams({"select": ",".join(columns)})
    else:
        method = RequestMethod.HEAD
        params = QueryParams()
    headers = Headers({"Prefer": f"count={count}"}) if count else Headers()
    return QueryArgs(method, params, headers, {})


def pre_insert(
    json: dict,
    *,
    count: Optional[CountMethod],
    returning: ReturnMethod,
    upsert: bool,
) -> QueryArgs:
    prefer_headers = [f"return={returning}"]
    if count:
        prefer_headers.append(f"count={count}")
    if upsert:
        prefer_headers.append("resolution=merge-duplicates")
    headers = Headers({"Prefer": ",".join(prefer_headers)})
    return QueryArgs(RequestMethod.POST, QueryParams(), headers, json)


def pre_upsert(
    json: dict,
    *,
    count: Optional[CountMethod],
    returning: ReturnMethod,
    ignore_duplicates: bool,
    on_conflict: str = "",
) -> QueryArgs:
    query_params = {}
    prefer_headers = [f"return={returning}"]
    if count:
        prefer_headers.append(f"count={count}")
    resolution = "ignore" if ignore_duplicates else "merge"
    prefer_headers.append(f"resolution={resolution}-duplicates")
    headers = Headers({"Prefer": ",".join(prefer_headers)})
    if on_conflict:
        query_params["on_conflict"] = on_conflict
    return QueryArgs(RequestMethod.POST, QueryParams(query_params), headers, json)


def pre_update(
    json: dict,
    *,
    count: Optional[CountMethod],
    returning: ReturnMethod,
) -> QueryArgs:
    prefer_headers = [f"return={returning}"]
    if count:
        prefer_headers.append(f"count={count}")
    headers = Headers({"Prefer": ",".join(prefer_headers)})
    return QueryArgs(RequestMethod.PATCH, QueryParams(), headers, json)


def pre_delete(
    *,
    count: Optional[CountMethod],
    returning: ReturnMethod,
) -> QueryArgs:
    prefer_headers = [f"return={returning}"]
    if count:
        prefer_headers.append(f"count={count}")
    headers = Headers({"Prefer": ",".join(prefer_headers)})
    return QueryArgs(RequestMethod.DELETE, QueryParams(), headers, {})


class APIResponse(BaseModel):
    data: List[Dict[str, Any]]
    """The data returned by the query."""
    count: Optional[int] = None
    """The number of rows returned."""

    @field_validator("data")
    @classmethod
    def raise_when_api_error(cls: Type[APIResponse], value: Any) -> Any:
        if isinstance(value, dict) and value.get("message"):
            raise ValueError("You are passing an API error to the data field.")
        return value

    @staticmethod
    def _get_count_from_content_range_header(
        content_range_header: str,
    ) -> Optional[int]:
        content_range = content_range_header.split("/")
        return None if len(content_range) < 2 else int(content_range[1])

    @staticmethod
    def _is_count_in_prefer_header(prefer_header: str) -> bool:
        pattern = f"count=({'|'.join([cm.value for cm in CountMethod])})"
        return bool(search(pattern, prefer_header))

    @classmethod
    def _get_count_from_http_request_response(
        cls: Type[APIResponse],
        request_response: RequestResponse,
    ) -> Optional[int]:
        prefer_header: Optional[str] = request_response.request.headers.get("prefer")
        if not prefer_header:
            return None
        is_count_in_prefer_header = cls._is_count_in_prefer_header(prefer_header)
        content_range_header: Optional[str] = request_response.headers.get(
            "content-range"
        )
        return (
            cls._get_count_from_content_range_header(content_range_header)
            if (is_count_in_prefer_header and content_range_header)
            else None
        )

    @classmethod
    def from_http_request_response(
        cls: Type[APIResponse], request_response: RequestResponse
    ) -> APIResponse:
        try:
            data = request_response.json()
        except JSONDecodeError as e:
            return cls(data=[], count=0)
        count = cls._get_count_from_http_request_response(request_response)
        return cls(data=data, count=count)

    @classmethod
    def from_dict(cls: Type[APIResponse], dict: Dict[str, Any]) -> APIResponse:
        keys = dict.keys()
        assert len(keys) == 3 and "data" in keys and "count" in keys and "error" in keys
        return cls(
            data=dict.get("data"), count=dict.get("count"), error=dict.get("error")
        )


class SingleAPIResponse(APIResponse):
    data: Dict[str, Any]  # type: ignore
    """The data returned by the query."""

    @classmethod
    def from_http_request_response(
        cls: Type[SingleAPIResponse], request_response: RequestResponse
    ) -> SingleAPIResponse:
        data = request_response.json()
        count = cls._get_count_from_http_request_response(request_response)
        return cls(data=data, count=count)

    @classmethod
    def from_dict(
        cls: Type[SingleAPIResponse], dict: Dict[str, Any]
    ) -> SingleAPIResponse:
        keys = dict.keys()
        assert len(keys) == 3 and "data" in keys and "count" in keys and "error" in keys
        return cls(
            data=dict.get("data"), count=dict.get("count"), error=dict.get("error")
        )


_FilterT = TypeVar("_FilterT", bound="BaseFilterRequestBuilder")


class BaseFilterRequestBuilder:
    def __init__(
        self,
        session: Union[AsyncClient, SyncClient],
        headers: Headers,
        params: QueryParams,
    ) -> None:
        self.session = session
        self.headers = headers
        self.params = params
        self.negate_next = False

    @property
    def not_(self: _FilterT) -> _FilterT:
        """Whether the filter applied next should be negated."""
        self.negate_next = True
        return self

    def filter(self: _FilterT, column: str, operator: str, criteria: str) -> _FilterT:
        """Apply filters on a query.

        Args:
            column: The name of the column to apply a filter on
            operator: The operator to use while filtering
            criteria: The value to filter by
        """
        if self.negate_next is True:
            self.negate_next = False
            operator = f"{Filters.NOT}.{operator}"
        key, val = sanitize_param(column), f"{operator}.{criteria}"
        self.params = self.params.add(key, val)
        return self

    def eq(self: _FilterT, column: str, value: Any) -> _FilterT:
        """An 'equal to' filter.

        Args:
            column: The name of the column to apply a filter on
            value: The value to filter by
        """
        return self.filter(column, Filters.EQ, value)

    def neq(self: _FilterT, column: str, value: Any) -> _FilterT:
        """A 'not equal to' filter

        Args:
            column: The name of the column to apply a filter on
            value: The value to filter by
        """
        return self.filter(column, Filters.NEQ, value)

    def gt(self: _FilterT, column: str, value: Any) -> _FilterT:
        """A 'greater than' filter

        Args:
            column: The name of the column to apply a filter on
            value: The value to filter by
        """
        return self.filter(column, Filters.GT, value)

    def gte(self: _FilterT, column: str, value: Any) -> _FilterT:
        """A 'greater than or equal to' filter

        Args:
            column: The name of the column to apply a filter on
            value: The value to filter by
        """
        return self.filter(column, Filters.GTE, value)

    def lt(self: _FilterT, column: str, value: Any) -> _FilterT:
        """A 'less than' filter

        Args:
            column: The name of the column to apply a filter on
            value: The value to filter by
        """
        return self.filter(column, Filters.LT, value)

    def lte(self: _FilterT, column: str, value: Any) -> _FilterT:
        """A 'less than or equal to' filter

        Args:
            column: The name of the column to apply a filter on
            value: The value to filter by
        """
        return self.filter(column, Filters.LTE, value)

    def is_(self: _FilterT, column: str, value: Any) -> _FilterT:
        """An 'is' filter

        Args:
            column: The name of the column to apply a filter on
            value: The value to filter by
        """
        return self.filter(column, Filters.IS, value)

    def like(self: _FilterT, column: str, pattern: str) -> _FilterT:
        """A 'LIKE' filter, to use for pattern matching.

        Args:
            column: The name of the column to apply a filter on
            pattern: The pattern to filter by
        """
        return self.filter(column, Filters.LIKE, pattern)

    def ilike(self: _FilterT, column: str, pattern: str) -> _FilterT:
        """An 'ILIKE' filter, to use for pattern matching (case insensitive).

        Args:
            column: The name of the column to apply a filter on
            pattern: The pattern to filter by
        """
        return self.filter(column, Filters.ILIKE, pattern)

    def fts(self: _FilterT, column: str, query: Any) -> _FilterT:
        return self.filter(column, Filters.FTS, query)

    def plfts(self: _FilterT, column: str, query: Any) -> _FilterT:
        return self.filter(column, Filters.PLFTS, query)

    def phfts(self: _FilterT, column: str, query: Any) -> _FilterT:
        return self.filter(column, Filters.PHFTS, query)

    def wfts(self: _FilterT, column: str, query: Any) -> _FilterT:
        return self.filter(column, Filters.WFTS, query)

    def in_(self: _FilterT, column: str, values: Iterable[Any]) -> _FilterT:
        values = map(sanitize_param, values)
        values = ",".join(values)
        return self.filter(column, Filters.IN, f"({values})")

    def cs(self: _FilterT, column: str, values: Iterable[Any]) -> _FilterT:
        values = ",".join(values)
        return self.filter(column, Filters.CS, f"{{{values}}}")

    def cd(self: _FilterT, column: str, values: Iterable[Any]) -> _FilterT:
        values = ",".join(values)
        return self.filter(column, Filters.CD, f"{{{values}}}")

    def contains(
        self: _FilterT, column: str, value: Union[Iterable[Any], str, Dict[Any, Any]]
    ) -> _FilterT:
        if isinstance(value, str):
            # range types can be inclusive '[', ']' or exclusive '(', ')' so just
            # keep it simple and accept a string
            return self.filter(column, Filters.CS, value)
        if not isinstance(value, dict) and isinstance(value, Iterable):
            # Expected to be some type of iterable
            stringified_values = ",".join(value)
            return self.filter(column, Filters.CS, f"{{{stringified_values}}}")

        return self.filter(column, Filters.CS, json.dumps(value))

    def contained_by(
        self: _FilterT, column: str, value: Union[Iterable[Any], str, Dict[Any, Any]]
    ) -> _FilterT:
        if isinstance(value, str):
            # range
            return self.filter(column, Filters.CD, value)
        if not isinstance(value, dict) and isinstance(value, Iterable):
            stringified_values = ",".join(value)
            return self.filter(column, Filters.CD, f"{{{stringified_values}}}")
        return self.filter(column, Filters.CD, json.dumps(value))

    def ov(self: _FilterT, column: str, values: Iterable[Any]) -> _FilterT:
        values = ",".join(values)
        return self.filter(column, Filters.OV, f"{{{values}}}")

    def sl(self: _FilterT, column: str, range: Tuple[int, int]) -> _FilterT:
        return self.filter(column, Filters.SL, f"({range[0]},{range[1]})")

    def sr(self: _FilterT, column: str, range: Tuple[int, int]) -> _FilterT:
        return self.filter(column, Filters.SR, f"({range[0]},{range[1]})")

    def nxl(self: _FilterT, column: str, range: Tuple[int, int]) -> _FilterT:
        return self.filter(column, Filters.NXL, f"({range[0]},{range[1]})")

    def nxr(self: _FilterT, column: str, range: Tuple[int, int]) -> _FilterT:
        return self.filter(column, Filters.NXR, f"({range[0]},{range[1]})")

    def adj(self: _FilterT, column: str, range: Tuple[int, int]) -> _FilterT:
        return self.filter(column, Filters.ADJ, f"({range[0]},{range[1]})")

    def match(self: _FilterT, query: Dict[str, Any]) -> _FilterT:
        updated_query = self

        if not query:
            raise ValueError(
                "query dictionary should contain at least one key-value pair"
            )

        for key, value in query.items():
            updated_query = self.eq(key, value)

        return updated_query


class BaseSelectRequestBuilder(BaseFilterRequestBuilder):
    def __init__(
        self,
        session: Union[AsyncClient, SyncClient],
        headers: Headers,
        params: QueryParams,
    ) -> None:
        BaseFilterRequestBuilder.__init__(self, session, headers, params)

    def explain(
        self: _FilterT,
        analyze: bool = False,
        verbose: bool = False,
        settings: bool = False,
        buffers: bool = False,
        wal: bool = False,
        format: str = "",
    ) -> _FilterT:
        options = [
            key
            for key, value in locals().items()
            if key not in ["self", "format"] and value
        ]
        options_str = "|".join(options)
        options_str = f'options="{options_str};"' if options_str else ""
        self.headers["Accept"] = f"application/vnd.pgrst.plan+{format}; {options_str}"
        return self

    def order(
        self: _FilterT,
        column: str,
        *,
        desc: bool = False,
        nullsfirst: bool = False,
        foreign_table: Optional[str] = None,
    ) -> _FilterT:
        """Sort the returned rows in some specific order.

        Args:
            column: The column to order by
            desc: Whether the rows should be ordered in descending order or not.
            nullsfirst: nullsfirst
            foreign_table: Foreign table name whose results are to be ordered.
        .. versionchanged:: 0.10.3
           Allow ordering results for foreign tables with the foreign_table parameter.
        """
        self.params = self.params.add(
            f"{foreign_table}.order" if foreign_table else "order",
            f"{column}{'.desc' if desc else ''}{'.nullsfirst' if nullsfirst else ''}",
        )
        return self

    def limit(
        self: _FilterT, size: int, *, foreign_table: Optional[str] = None
    ) -> _FilterT:
        """Limit the number of rows returned by a query.

        Args:
            size: The number of rows to be returned
            foreign_table: Foreign table name to limit
        .. versionchanged:: 0.10.3
           Allow limiting results returned for foreign tables with the foreign_table parameter.
        """
        self.params = self.params.add(
            f"{foreign_table}.limit" if foreign_table else "limit",
            size,
        )
        return self

    def range(self: _FilterT, start: int, end: int) -> _FilterT:
        self.headers["Range-Unit"] = "items"
        self.headers["Range"] = f"{start}-{end - 1}"
        return self




class AsyncPostgrestClient(BasePostgrestClient):
    """PostgREST client."""

    def __init__(
        self,
        base_url: str,
        *,
        #schema: str = "public",
        schema: str = "api",
        headers: Dict[str, str] = DEFAULT_POSTGREST_CLIENT_HEADERS,
        timeout: Union[int, float, Timeout] = DEFAULT_POSTGREST_CLIENT_TIMEOUT,
    ) -> None:
        BasePostgrestClient.__init__(
            self,
            base_url,
            schema=schema,
            headers=headers,
            timeout=timeout,
        )
        self.session = cast(AsyncClient, self.session)

    def create_session(
        self,
        base_url: str,
        headers: Dict[str, str],
        timeout: Union[int, float, Timeout],
    ) -> AsyncClient:
        return AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )

    async def __aenter__(self) -> AsyncPostgrestClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP connections."""
        await self.session.aclose()

    def from_(self, table: str) -> AsyncRequestBuilder:
        """Perform a table operation.

        Args:
            table: The name of the table
        Returns:
            :class:`AsyncRequestBuilder`
        """
        return AsyncRequestBuilder(self.session, f"/{table}")

    def table(self, table: str) -> AsyncRequestBuilder:
        """Alias to :meth:`from_`."""
        return self.from_(table)

    #@deprecated("0.2.0", "1.0.0", "Use self.from_() instead")
    def from_table(self, table: str) -> AsyncRequestBuilder:
        """Alias to :meth:`from_`."""
        return self.from_(table)

    async def rpc(self, func: str, params: dict) -> AsyncFilterRequestBuilder:
        """Perform a stored procedure call.

        Args:
            func: The name of the remote procedure to run.
            params: The parameters to be passed to the remote procedure.
        Returns:
            :class:`AsyncFilterRequestBuilder`
        Example:
            .. code-block:: python

                await client.rpc("foobar", {"arg": "value"}).execute()

        .. versionchanged:: 0.11.0
            This method now returns a :class:`AsyncFilterRequestBuilder` which allows you to
            filter on the RPC's resultset.
        """
        # the params here are params to be sent to the RPC and not the queryparams!
        return AsyncFilterRequestBuilder(
            self.session, f"/rpc/{func}", "POST", Headers(), QueryParams(), json=params
        )







class AsyncQueryRequestBuilder:
    def __init__(
        self,
        session: AsyncClient,
        path: str,
        http_method: str,
        headers: Headers,
        params: QueryParams,
        json: dict,
    ) -> None:
        self.session = session
        self.path = path
        self.http_method = http_method
        self.headers = headers
        self.params = params
        self.json = json

    async def execute(self) -> APIResponse:
        """Execute the query.

        .. tip::
            This is the last method called, after the query is built.

        Returns:
            :class:`APIResponse`

        Raises:
            :class:`APIError` If the API raised an error.
        """
        r = await self.session.request(
            self.http_method,
            self.path,
            json=self.json,
            params=self.params,
            headers=self.headers,
        )
        try:
            if (
                200 <= r.status_code <= 299
            ):  # Response.ok from JS (https://developer.mozilla.org/en-US/docs/Web/API/Response/ok)
                return APIResponse.from_http_request_response(r)
            else:
                raise APIError(r.json())
        except ValidationError as e:
            raise APIError(r.json()) from e
        except JSONDecodeError as e:
            raise APIError(generate_default_error_message(r))


class AsyncSingleRequestBuilder:
    def __init__(
        self,
        session: AsyncClient,
        path: str,
        http_method: str,
        headers: Headers,
        params: QueryParams,
        json: dict,
    ) -> None:
        self.session = session
        self.path = path
        self.http_method = http_method
        self.headers = headers
        self.params = params
        self.json = json

    async def execute(self) -> SingleAPIResponse:
        """Execute the query.

        .. tip::
            This is the last method called, after the query is built.

        Returns:
            :class:`SingleAPIResponse`

        Raises:
            :class:`APIError` If the API raised an error.
        """
        r = await self.session.request(
            self.http_method,
            self.path,
            json=self.json,
            params=self.params,
            headers=self.headers,
        )
        try:
            if (
                200 <= r.status_code <= 299
            ):  # Response.ok from JS (https://developer.mozilla.org/en-US/docs/Web/API/Response/ok)
                return SingleAPIResponse.from_http_request_response(r)
            else:
                raise APIError(r.json())
        except ValidationError as e:
            raise APIError(r.json()) from e
        except JSONDecodeError as e:
            raise APIError(generate_default_error_message(r))


class AsyncMaybeSingleRequestBuilder(AsyncSingleRequestBuilder):
    async def execute(self) -> Optional[SingleAPIResponse]:
        r = None
        try:
            r = await super().execute()
        except APIError as e:
            if e.details and "The result contains 0 rows" in e.details:
                return None
        if not r:
            raise APIError(
                {
                    "message": "Missing response",
                    "code": "204",
                    "hint": "Please check traceback of the code",
                    "details": "Postgrest couldn't retrieve response, please check traceback of the code. Please create an issue in `supabase-community/postgrest-py` if needed.",
                }
            )
        return r


# ignoring type checking as a workaround for https://github.com/python/mypy/issues/9319
class AsyncFilterRequestBuilder(BaseFilterRequestBuilder, AsyncQueryRequestBuilder):  # type: ignore
    def __init__(
        self,
        session: AsyncClient,
        path: str,
        http_method: str,
        headers: Headers,
        params: QueryParams,
        json: dict,
    ) -> None:
        BaseFilterRequestBuilder.__init__(self, session, headers, params)
        AsyncQueryRequestBuilder.__init__(
            self, session, path, http_method, headers, params, json
        )


# ignoring type checking as a workaround for https://github.com/python/mypy/issues/9319
class AsyncSelectRequestBuilder(BaseSelectRequestBuilder, AsyncQueryRequestBuilder):  # type: ignore
    def __init__(
        self,
        session: AsyncClient,
        path: str,
        http_method: str,
        headers: Headers,
        params: QueryParams,
        json: dict,
    ) -> None:
        BaseSelectRequestBuilder.__init__(self, session, headers, params)
        AsyncQueryRequestBuilder.__init__(
            self, session, path, http_method, headers, params, json
        )

    def single(self) -> AsyncSingleRequestBuilder:
        """Specify that the query will only return a single row in response.

        .. caution::
            The API will raise an error if the query returned more than one row.
        """
        self.headers["Accept"] = "application/vnd.pgrst.object+json"
        return AsyncSingleRequestBuilder(
            headers=self.headers,
            http_method=self.http_method,
            json=self.json,
            params=self.params,
            path=self.path,
            session=self.session,  # type: ignore
        )

    def maybe_single(self) -> AsyncMaybeSingleRequestBuilder:
        """Retrieves at most one row from the result. Result must be at most one row (e.g. using `eq` on a UNIQUE column), otherwise this will result in an error."""
        self.headers["Accept"] = "application/vnd.pgrst.object+json"
        return AsyncMaybeSingleRequestBuilder(
            headers=self.headers,
            http_method=self.http_method,
            json=self.json,
            params=self.params,
            path=self.path,
            session=self.session,  # type: ignore
        )

    def text_search(
        self, column: str, query: str, options: Dict[str, any] = {}
    ) -> AsyncFilterRequestBuilder:
        type_ = options.get("type")
        type_part = ""
        if type_ == "plain":
            type_part = "pl"
        elif type_ == "phrase":
            type_part = "ph"
        elif type_ == "web_search":
            type_part = "w"
        config_part = f"({options.get('config')})" if options.get("config") else ""
        self.params = self.params.add(column, f"{type_part}fts{config_part}.{query}")

        return AsyncQueryRequestBuilder(
            headers=self.headers,
            http_method=self.http_method,
            json=self.json,
            params=self.params,
            path=self.path,
            session=self.session,  # type: ignore
        )


class AsyncRequestBuilder:
    def __init__(self, session: AsyncClient, path: str) -> None:
        self.session = session
        self.path = path

    def select(
        self,
        *columns: str,
        count: Optional[CountMethod] = None,
    ) -> AsyncSelectRequestBuilder:
        """Run a SELECT query.

        Args:
            *columns: The names of the columns to fetch.
            count: The method to use to get the count of rows returned.
        Returns:
            :class:`AsyncSelectRequestBuilder`
        """
        method, params, headers, json = pre_select(*columns, count=count)
        return AsyncSelectRequestBuilder(
            self.session, self.path, method, headers, params, json
        )

    def insert(
        self,
        json: Union[dict, list],
        *,
        count: Optional[CountMethod] = None,
        returning: ReturnMethod = ReturnMethod.representation,
        upsert: bool = False,
    ) -> AsyncQueryRequestBuilder:
        """Run an INSERT query.

        Args:
            json: The row to be inserted.
            count: The method to use to get the count of rows returned.
            returning: Either 'minimal' or 'representation'
            upsert: Whether the query should be an upsert.
        Returns:
            :class:`AsyncQueryRequestBuilder`
        """
        method, params, headers, json = pre_insert(
            json,
            count=count,
            returning=returning,
            upsert=upsert,
        )
        return AsyncQueryRequestBuilder(
            self.session, self.path, method, headers, params, json
        )

    def upsert(
        self,
        json: dict,
        *,
        count: Optional[CountMethod] = None,
        returning: ReturnMethod = ReturnMethod.representation,
        ignore_duplicates: bool = False,
        on_conflict: str = "",
    ) -> AsyncQueryRequestBuilder:
        """Run an upsert (INSERT ... ON CONFLICT DO UPDATE) query.

        Args:
            json: The row to be inserted.
            count: The method to use to get the count of rows returned.
            returning: Either 'minimal' or 'representation'
            ignore_duplicates: Whether duplicate rows should be ignored.
            on_conflict: Specified columns to be made to work with UNIQUE constraint.
        Returns:
            :class:`AsyncQueryRequestBuilder`
        """
        method, params, headers, json = pre_upsert(
            json,
            count=count,
            returning=returning,
            ignore_duplicates=ignore_duplicates,
            on_conflict=on_conflict,
        )
        return AsyncQueryRequestBuilder(
            self.session, self.path, method, headers, params, json
        )

    def update(
        self,
        json: dict,
        *,
        count: Optional[CountMethod] = None,
        returning: ReturnMethod = ReturnMethod.representation,
    ) -> AsyncFilterRequestBuilder:
        """Run an UPDATE query.

        Args:
            json: The updated fields.
            count: The method to use to get the count of rows returned.
            returning: Either 'minimal' or 'representation'
        Returns:
            :class:`AsyncFilterRequestBuilder`
        """
        method, params, headers, json = pre_update(
            json,
            count=count,
            returning=returning,
        )
        return AsyncFilterRequestBuilder(
            self.session, self.path, method, headers, params, json
        )

    def delete(
        self,
        *,
        count: Optional[CountMethod] = None,
        returning: ReturnMethod = ReturnMethod.representation,
    ) -> AsyncFilterRequestBuilder:
        """Run a DELETE query.

        Args:
            count: The method to use to get the count of rows returned.
            returning: Either 'minimal' or 'representation'
        Returns:
            :class:`AsyncFilterRequestBuilder`
        """
        method, params, headers, json = pre_delete(
            count=count,
            returning=returning,
        )
        return AsyncFilterRequestBuilder(
            self.session, self.path, method, headers, params, json
        )

    def stub(self):
        return None



if __name__ == '__main__':
    
    async def main():
        async with AsyncPostgrestClient("http://localhost:3000") as client:
            #r = await client.from_("book_details").select("*").execute()
            #r = await client.from_("book_details").select("book_id", "title").execute()
            #await client.from_("book_details").update({"title": "fara"}).eq("author", "").execute()
            await client.rpc("foobar", {"arg1": "value1", "arg2": "value2"}).execute()
            #print(client.schema)
            #books = r.data
            #print(books)

    asyncio.run(main())



