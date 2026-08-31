from django.db.models.deletion import ProtectedError
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from core.automations.models import SocietaryBriefing, SocietaryBriefingStatus


@receiver(
    pre_delete,
    sender=SocietaryBriefing,
    dispatch_uid="protect_completed_societary_briefing_evidence",
)
def protect_completed_societary_briefing(
    sender: type[SocietaryBriefing],
    instance: SocietaryBriefing,
    **kwargs: object,
) -> None:
    del sender, kwargs
    if instance.status == SocietaryBriefingStatus.COMPLETED:
        raise ProtectedError(
            "Um briefing concluído é evidência imutável e não pode ser excluído.",
            {instance},
        )
