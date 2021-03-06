from typing import Optional, Dict, Any
from fastapi import HTTPException, Header, FastAPI, status, Response, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from grpclib import GRPCError
from core.middlewares.sentry import SentryMiddleware
from core.middlewares.locale import LocaleMiddleware
from providers import provide_by_language, providers_port_mapping, create_client, close_client
from common import utils
from datetime import datetime
from translation import translate
from config import DEFAULT_MAX_AGE
from core.cache import cache
from frozendict import frozendict
from definitions.scraper import ScraperStub
from log_config import init_logger
import logging
from responses.search import SuccessResponse, ConflictResponse
import uvicorn

logger: logging.Logger = init_logger()
PROVIDERS: Dict[str, ScraperStub] = None

app = FastAPI()

# app.add_middleware(HTTPSRedirectMiddleware)
app.add_middleware(SentryMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1)
app.add_middleware(LocaleMiddleware)


@app.on_event("startup")
async def startup_event():
    """Startup function called at the start of the server"""
    global PROVIDERS
    PROVIDERS = frozendict({k: create_client("localhost", port) for k, port in providers_port_mapping.items()})
    logging.info("Initialized providers")


@app.on_event("shutdown")
async def shutdown_event():
    """The shutdown function called at the shutdown of the server"""
    for stub in PROVIDERS.values():
        close_client(stub)


@app.get("/search", response_model=SuccessResponse, responses={status.HTTP_409_CONFLICT: {"model": ConflictResponse}})
async def search(req: Request, res: Response, q: str, long: bool = False, cache_control: Optional[str] = Header(None)):
    """The search endpoint.

    Args:
        req (Request): The http request
        res (Response): The http response
        q (str): The text to search
        long (bool, optional): If the search is long or short. Defaults to False.
        cache_control (Optional[str], optional): The value of cache_control http header. Defaults to Header(None).

    Raises:
        HTTPException: An exception for internal error, results not found and conflicting results in search.

    Returns:
        SuccessResponse: The result of the search
    """
    result = None
    if cache_control is None or not (cache_control.startswith("max-age=") or cache_control.startswith("no-cache")):
        result = cache.retrieve(q, long, req.state.lang, DEFAULT_MAX_AGE)
    elif cache_control.startswith("max-age="):
        result = cache.retrieve(q, long, req.state.lang, int(cache_control.replace("max-age=", "")))

    if result is None:
        # Retrieve from services

        result_provider = None
        for p, stub in provide_by_language(PROVIDERS, req.state.lang):
            # iterate over providers
            result_provider = p
            try:
                if long:
                    result = await stub.long_search(text=q)
                else:
                    result = await stub.search(text=q)
            except GRPCError as e:
                logger.info(f"Provider '{p}' failed to find '{q}'\n"
                            f"Error code: '{e.status}'\n"
                            f"Message: '{e.message}'" if hasattr(e, 'message') else "")
            except ConnectionRefusedError as e:
                logger.error(f"Unable to connect to provider '{p}': Connection Refused")
            except Exception as e:
                logger.error(e)
            if result is not None:
                logger.info(f"Received {result}")
                result = result.to_dict()
                break

        # Find result_provider
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="result_not_found")
        if result.get("disambiguous"):
            return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"data": result['disambiguousData'], "provider": result_provider})

        result = {
            "data": result["data"].strip(),
            "provider": result_provider,
            "current_language": result["language"],
            "original_language": result["language"],
            "created_at": datetime.now()
        }
        cache.add(q, result_provider, long, result["current_language"], result)
    if result["current_language"] != req.state.lang:
        result = translate(result, req.state.lang)
        cache.add(q, result["provider"], long, req.state.lang, result)
    return result


@app.get("/search/{provider}", response_model=SuccessResponse, responses={status.HTTP_409_CONFLICT: {"model": ConflictResponse}})
async def search_provider(req: Request, res: Response, q: str, provider: str, long: bool = False, cache_control: Optional[str] = Header(None)):
    """The search endpoint with specific provider.

    Args:
        req (Request): The http request
        res (Response): The http response
        q (str): The text to search
        provider (str): the desired provider
        long (bool, optional): If the search is long or short. Defaults to False.
        cache_control (Optional[str], optional): The value of cache_control http header. Defaults to Header(None).

    Raises:
        HTTPException: An exception for internal error, results not found, conflicting results in search and provider unavailability.

    Returns:
        SuccessResponse: The result of the search
    """
    provider = utils.sanitize_string(provider)
    if not provider in PROVIDERS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="provider_not_available")

    result = None
    if cache_control is None or not (cache_control.startswith("max-age=") or cache_control.startswith("no-cache")):
        result = cache.retrieve(q, long, req.state.lang, DEFAULT_MAX_AGE, provider)
    elif cache_control.startswith("max-age="):
        result = cache.retrieve(q, long, req.state.lang, int(cache_control.replace("max-age=", "")), provider)

    if result is None:
        # Retrieve from services
        try:
            if long:
                result = await PROVIDERS[provider].long_search(text=q)
            else:
                result = await PROVIDERS[provider].search(text=q)
        except GRPCError as e:
            logger.info(f"Provider '{provider}' failed to find '{q}'\n"
                           f"Error code: '{e.status}'\n"
                           f"Message: '{e.message}'" if hasattr(e, 'message') else "")
        except ConnectionRefusedError as e:
            logger.error(f"Unable to connect to provider '{provider}': Connection Refused")
        except Exception as e:
            logger.error(e)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="unknown_error")
        # Find result_provider
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="result_not_found")
        logger.info(f"Received {result}")
        result = result.to_dict()
        if result.get("disambiguous"):
            return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"data": result['disambiguousData'], "provider": provider})

        result = {
            "data": result["data"].strip(),
            "provider": provider,
            "current_language": result["language"],
            "original_language": result["language"],
            "created_at": datetime.now()
        }
        cache.add(q, provider, long, result["current_language"], result)
    if result["current_language"] != req.state.lang:
        result = translate(result, req.state.lang)
        cache.add(q, result["provider"], long, req.state.lang, result)
    return result


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
