from fastapi import APIRouter, HTTPException
from backend import database
from backend.models import (
    ProviderCreate,
    ProviderUpdate,
    ProviderResponse,
    ProviderListResponse,
)

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("", response_model=ProviderListResponse)
async def get_providers():
    return ProviderListResponse(providers=await database.list_providers())


@router.post("", response_model=ProviderResponse)
async def add_provider(body: ProviderCreate):
    return await database.create_provider(
        name=body.name,
        endpoint=body.endpoint,
        api_key=body.api_key,
        model=body.model,
        is_default=body.is_default,
    )


@router.put("/{provider_id}", response_model=ProviderResponse)
async def edit_provider(provider_id: int, body: ProviderUpdate):
    provider = await database.update_provider(provider_id, **body.model_dump(exclude_unset=True))
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider


@router.delete("/{provider_id}")
async def remove_provider(provider_id: int):
    existing = await database.get_provider(provider_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    await database.delete_provider(provider_id)
    return {"status": "deleted"}
