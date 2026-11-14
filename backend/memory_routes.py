import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from pydantic import ValidationError

from identity_service import (
    IdentityResourceNotFound,
    update_relationship_memory_mode,
    update_user_memory_mode,
)
from memory_models import (
    FamiliarityCandidateDocument,
    MemoryDocument,
    MemoryMode,
    MemoryModel,
    RelationshipDocument,
    UserDocument,
)
from familiarity_service import (
    CandidateConfirmationResult,
    FamiliarityCandidateConflict,
    FamiliarityCandidateNotFound,
    MemoryStorageDisabled,
    confirm_candidate,
    list_pending_candidates,
    reject_candidate,
)
from memory_service import (
    InvalidMemoryScope,
    MemoryCorrectionInput,
    MemoryCreateInput,
    MemoryDeletionResult,
    MemoryWriteResult,
    MemoryWriteStatus,
    ObservationCreateInput,
    ObservationWriteResult,
    correct_memory,
    create_memory,
    delete_all_user_memory,
    delete_companion_memories,
    delete_memory,
    delete_shared_memories,
    record_observation,
)


logger = logging.getLogger(__name__)
router = APIRouter(tags=["memory"])


class UpdateUserMemoryModeRequest(MemoryModel):
    default_mode: MemoryMode | None


class UpdateRelationshipMemoryModeRequest(MemoryModel):
    memory_mode_override: MemoryMode | None


def _database(request: Request):
    database = getattr(request.app.state, "database", None)
    if database is None or not getattr(
        request.app.state,
        "memory_store_initialized",
        False,
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory storage is unavailable",
        )
    return database


def _invoke(service, *args):
    try:
        return service(*args)
    except IdentityResourceNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except InvalidMemoryScope as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error
    except FamiliarityCandidateNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except (FamiliarityCandidateConflict, MemoryStorageDisabled) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stored memory data is invalid",
        ) from error
    except PyMongoError as error:
        logger.exception("MongoDB memory operation failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory storage is temporarily unavailable.",
        ) from error


def _operation_response(result):
    if result.status == MemoryWriteStatus.CREATED:
        status_code = status.HTTP_201_CREATED
    elif result.status == MemoryWriteStatus.APPROVAL_REQUIRED:
        status_code = status.HTTP_202_ACCEPTED
    else:
        status_code = status.HTTP_200_OK
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(result),
    )


@router.post(
    "/users/{user_id}/memories",
    response_model=MemoryWriteResult,
)
def create_memory_endpoint(
    user_id: UUID,
    body: MemoryCreateInput,
    request: Request,
):
    result = _invoke(create_memory, _database(request), str(user_id), body)
    return _operation_response(result)


@router.post(
    "/users/{user_id}/observations",
    response_model=ObservationWriteResult,
)
def record_observation_endpoint(
    user_id: UUID,
    body: ObservationCreateInput,
    request: Request,
):
    result = _invoke(
        record_observation,
        _database(request),
        str(user_id),
        body,
    )
    return _operation_response(result)


@router.patch(
    "/users/{user_id}/memory-preference",
    response_model=UserDocument,
)
def update_user_memory_mode_endpoint(
    user_id: UUID,
    body: UpdateUserMemoryModeRequest,
    request: Request,
):
    return _invoke(
        update_user_memory_mode,
        _database(request),
        str(user_id),
        body.default_mode,
    )


@router.patch(
    "/users/{user_id}/relationships/{companion_id}/memory-mode",
    response_model=RelationshipDocument,
)
def update_relationship_memory_mode_endpoint(
    user_id: UUID,
    companion_id: str,
    body: UpdateRelationshipMemoryModeRequest,
    request: Request,
):
    return _invoke(
        update_relationship_memory_mode,
        _database(request),
        str(user_id),
        companion_id,
        body.memory_mode_override,
    )


@router.patch(
    "/users/{user_id}/memories/{memory_id}",
    response_model=MemoryDocument,
)
def correct_memory_endpoint(
    user_id: UUID,
    memory_id: UUID,
    body: MemoryCorrectionInput,
    request: Request,
):
    return _invoke(
        correct_memory,
        _database(request),
        str(user_id),
        str(memory_id),
        body,
    )


@router.delete(
    "/users/{user_id}/memories/shared",
    response_model=MemoryDeletionResult,
)
def delete_shared_memories_endpoint(user_id: UUID, request: Request):
    return _invoke(delete_shared_memories, _database(request), str(user_id))


@router.delete(
    "/users/{user_id}/memories",
    response_model=MemoryDeletionResult,
)
def delete_all_user_memory_endpoint(user_id: UUID, request: Request):
    return _invoke(delete_all_user_memory, _database(request), str(user_id))


@router.delete(
    "/users/{user_id}/relationships/{companion_id}/memories",
    response_model=MemoryDeletionResult,
)
def delete_companion_memories_endpoint(
    user_id: UUID,
    companion_id: str,
    request: Request,
):
    return _invoke(
        delete_companion_memories,
        _database(request),
        str(user_id),
        companion_id,
    )


@router.delete(
    "/users/{user_id}/memories/{memory_id}",
    response_model=MemoryDeletionResult,
)
def delete_memory_endpoint(
    user_id: UUID,
    memory_id: UUID,
    request: Request,
):
    return _invoke(
        delete_memory,
        _database(request),
        str(user_id),
        str(memory_id),
    )


@router.get(
    "/users/{user_id}/familiarity-candidates",
    response_model=list[FamiliarityCandidateDocument],
)
def list_user_familiarity_candidates_endpoint(user_id: UUID, request: Request):
    return _invoke(list_pending_candidates, _database(request), str(user_id))


@router.get(
    "/users/{user_id}/companions/{companion_id}/familiarity-candidates",
    response_model=list[FamiliarityCandidateDocument],
)
def list_companion_familiarity_candidates_endpoint(
    user_id: UUID,
    companion_id: str,
    request: Request,
):
    return _invoke(
        list_pending_candidates,
        _database(request),
        str(user_id),
        companion_id,
    )


class ConfirmFamiliarityCandidateRequest(MemoryModel):
    share_across_companions: bool = False


@router.post(
    "/users/{user_id}/familiarity-candidates/{candidate_id}/confirm",
    response_model=CandidateConfirmationResult,
)
def confirm_familiarity_candidate_endpoint(
    user_id: UUID,
    candidate_id: UUID,
    request: Request,
    body: ConfirmFamiliarityCandidateRequest = ConfirmFamiliarityCandidateRequest(),
):
    return _invoke(
        confirm_candidate,
        _database(request),
        str(user_id),
        str(candidate_id),
        body.share_across_companions,
    )


@router.post(
    "/users/{user_id}/familiarity-candidates/{candidate_id}/reject",
    response_model=FamiliarityCandidateDocument,
)
def reject_familiarity_candidate_endpoint(
    user_id: UUID,
    candidate_id: UUID,
    request: Request,
):
    return _invoke(
        reject_candidate,
        _database(request),
        str(user_id),
        str(candidate_id),
    )