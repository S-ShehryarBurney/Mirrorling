import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pymongo.errors import PyMongoError

from memory_models import (
    BUILT_IN_COMPANION_IDS,
    CompanionDocument,
    FirstMeetingStatus,
    MemoryMode,
    RelationshipDocument,
    UserDocument,
)
from identity_service import (
    DuplicateRelationship,
    IdentityResourceNotFound,
    InvalidFirstMeetingTransition,
    RelationshipUpdateConflict,
    create_custom_companion,
    create_relationship,
    create_user,
    get_companion,
    get_user,
    list_companions,
    list_relationships,
    update_first_meeting_status,
    update_last_interaction,
)


router = APIRouter(tags=["identity"])
logger = logging.getLogger(__name__)


class IdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateCompanionRequest(IdentityRequest):
    name: str = Field(min_length=1)
    personality_description: str = Field(min_length=1)


class CreateRelationshipRequest(IdentityRequest):
    companion_id: str
    memory_mode_override: MemoryMode | None = None

    @field_validator("companion_id")
    @classmethod
    def validate_companion_id(cls, value: str) -> str:
        if value in BUILT_IN_COMPANION_IDS:
            return value
        try:
            return str(UUID(value))
        except (TypeError, ValueError, AttributeError) as error:
            raise ValueError("companion_id must be a built-in ID or UUID") from error


class FirstMeetingStatusRequest(IdentityRequest):
    status: FirstMeetingStatus


def _database(request: Request):
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MongoDB is unavailable",
        )
    return database


def _invoke(service, *args, **kwargs):
    try:
        return service(*args, **kwargs)
    except IdentityResourceNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except DuplicateRelationship as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Relationship already exists",
        ) from error
    except (InvalidFirstMeetingTransition, RelationshipUpdateConflict) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stored identity data is invalid",
        ) from error
    except PyMongoError as error:
        logger.exception("MongoDB identity operation failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity storage is temporarily unavailable",
        ) from error


@router.post(
    "/users",
    response_model=UserDocument,
    status_code=status.HTTP_201_CREATED,
)
def create_user_endpoint(request: Request):
    return create_user(_database(request))


@router.get("/users/{user_id}", response_model=UserDocument)
def get_user_endpoint(user_id: UUID, request: Request):
    return _invoke(get_user, _database(request), str(user_id))


@router.post(
    "/users/{user_id}/companions",
    response_model=CompanionDocument,
    status_code=status.HTTP_201_CREATED,
)
def create_custom_companion_endpoint(
    user_id: UUID,
    body: CreateCompanionRequest,
    request: Request,
):
    return _invoke(
        create_custom_companion,
        _database(request),
        str(user_id),
        body.name,
        body.personality_description,
    )


@router.get(
    "/users/{user_id}/companions",
    response_model=list[CompanionDocument],
)
def list_companions_endpoint(user_id: UUID, request: Request):
    return _invoke(list_companions, _database(request), str(user_id))


@router.get(
    "/users/{user_id}/companions/{companion_id}",
    response_model=CompanionDocument,
)
def get_companion_endpoint(
    user_id: UUID,
    companion_id: str,
    request: Request,
):
    return _invoke(
        get_companion,
        _database(request),
        str(user_id),
        companion_id,
    )


@router.post(
    "/users/{user_id}/relationships",
    response_model=RelationshipDocument,
    status_code=status.HTTP_201_CREATED,
)
def create_relationship_endpoint(
    user_id: UUID,
    body: CreateRelationshipRequest,
    request: Request,
):
    return _invoke(
        create_relationship,
        _database(request),
        str(user_id),
        body.companion_id,
        body.memory_mode_override,
    )


@router.get(
    "/users/{user_id}/relationships",
    response_model=list[RelationshipDocument],
)
def list_relationships_endpoint(user_id: UUID, request: Request):
    return _invoke(list_relationships, _database(request), str(user_id))


@router.post(
    "/users/{user_id}/relationships/{companion_id}/interactions",
    response_model=RelationshipDocument,
)
def update_interaction_endpoint(
    user_id: UUID,
    companion_id: str,
    request: Request,
):
    return _invoke(
        update_last_interaction,
        _database(request),
        str(user_id),
        companion_id,
    )


@router.patch(
    "/users/{user_id}/relationships/{companion_id}/first-meeting",
    response_model=RelationshipDocument,
)
def update_first_meeting_endpoint(
    user_id: UUID,
    companion_id: str,
    body: FirstMeetingStatusRequest,
    request: Request,
):
    return _invoke(
        update_first_meeting_status,
        _database(request),
        str(user_id),
        companion_id,
        body.status,
    )