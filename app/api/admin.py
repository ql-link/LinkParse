import logging
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.security import authenticate
from app.schemas.models import RuntimeConfig
from app.services.runtime_config import RuntimeConfigStore

router = APIRouter(
    prefix="/v1/admin", tags=["admin"], dependencies=[Depends(authenticate)]
)
logger = logging.getLogger("linkparse.admin")


@router.get("/config")
def get_runtime_config(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    result = RuntimeConfigStore(settings).describe()
    result["restart_notes"] = {
        "task_time_limit_seconds": "Celery hard timeout takes full effect after worker restart",
        "ort_threads": "New parser instances use the updated thread limits immediately",
    }
    return result


@router.put("/config")
def update_runtime_config(
    config: RuntimeConfig,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    result = RuntimeConfigStore(settings).write(config)
    logger.info("runtime_config_updated fields=%s", ",".join(RuntimeConfig.model_fields))
    return result


@router.delete("/config")
def reset_runtime_config(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    result = RuntimeConfigStore(settings).reset()
    logger.info("runtime_config_reset")
    return result
