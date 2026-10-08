from dataclasses import dataclass
import json
import logging

from app.core.config import Settings

logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class VerificationEmail:
    email: str
    code: str
    purpose: str


class EmailDeliveryError(RuntimeError):
    pass


class EmailDeliveryProvider:
    async def send_verification(self, message: VerificationEmail) -> None:
        raise NotImplementedError


class ConsoleEmailDeliveryProvider(EmailDeliveryProvider):
    async def send_verification(self, message: VerificationEmail) -> None:
        logger.info(
            "verification_code provider=console email=%s purpose=%s code=%s",
            message.email,
            message.purpose,
            message.code,
        )


class TencentSesEmailDeliveryProvider(EmailDeliveryProvider):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send_verification(self, message: VerificationEmail) -> None:
        try:
            from tencentcloud.common import credential
            from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
                TencentCloudSDKException,
            )
            from tencentcloud.ses.v20201002 import models, ses_client
        except ImportError as exc:
            raise EmailDeliveryError("tencent_ses_sdk_missing") from exc

        if not self._settings.tencent_cloud_secret_id or not self._settings.tencent_cloud_secret_key:
            raise EmailDeliveryError("tencent_ses_credentials_missing")
        if not self._settings.tencent_ses_from_email:
            raise EmailDeliveryError("tencent_ses_from_email_missing")

        cred = credential.Credential(
            self._settings.tencent_cloud_secret_id,
            self._settings.tencent_cloud_secret_key,
        )
        client = ses_client.SesClient(cred, self._settings.tencent_ses_region)
        request = models.SendEmailRequest()
        request.FromEmailAddress = self._settings.tencent_ses_from_email
        request.Destination = [message.email]
        request.Subject = "KigCraft verification code"

        if self._settings.tencent_ses_template_id:
            try:
                template_id = int(self._settings.tencent_ses_template_id)
            except ValueError as exc:
                raise EmailDeliveryError("tencent_ses_template_id_invalid") from exc
            template = models.Template()
            template.TemplateID = template_id
            logo_url = self._settings.tencent_ses_logo_url.strip()
            template_data: dict[str, str] = {
                "code": message.code,
                "purpose": message.purpose,
            }
            if logo_url:
                template_data["logo_url"] = logo_url
                template_data["logo"] = logo_url
            template.TemplateData = json.dumps(
                template_data,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            request.Template = template
        else:
            request.Simple = models.Simple()
            request.Simple.Html = (
                "<p>Your KigCraft verification code is <strong>%s</strong>.</p>"
                "<p>Purpose: %s</p>"
            ) % (message.code, message.purpose)
            request.Simple.Text = (
                "Your KigCraft verification code is %s. Purpose: %s."
                % (message.code, message.purpose)
            )

        try:
            client.SendEmail(request)
        except TencentCloudSDKException as exc:
            raise EmailDeliveryError("tencent_ses_send_failed") from exc


def get_email_delivery_provider(settings: Settings) -> EmailDeliveryProvider:
    provider = settings.email_provider.strip().lower()
    if provider == "console":
        return ConsoleEmailDeliveryProvider()
    if provider == "tencent_ses":
        return TencentSesEmailDeliveryProvider(settings)
    raise EmailDeliveryError(f"unsupported_email_provider:{provider}")
