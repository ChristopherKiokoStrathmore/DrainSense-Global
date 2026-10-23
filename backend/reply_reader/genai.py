"""OCI Generative AI client for the reply reader (ARCHITECTURE M5: Cohere Command A, Frankfurt).

"Generative AI only reads": the fixed prompt asks the model to extract fields from a farmer's or
herder's reply into the §2.4 JSON, never to answer or advise. The output is validated in
reply_reader/schema.py; any failure sends the report to the review queue.
"""
import json
import logging
import re
import threading

from django.conf import settings

from reply_reader.schema import JSON_SCHEMA

logger = logging.getLogger(__name__)

PREAMBLE = """You read SMS replies sent to Ngao, a Kenyan early-warning service for maize farmers and
livestock herders. Replies are in Kiswahili, English or a mix (Sheng). Your only job is to EXTRACT
facts into JSON. Never answer the sender, never give advice, never add fields.

Return ONLY one JSON object with exactly these keys:
report_type: one of wet_bags, sick_animals, dead_animals, flooding, water_point_dry, harvested, rain,
  bags_dried, rvf_signs, booking_confirm, stop, other
count: number or null (bags or animals mentioned)
unit: "bags", "animals" or null
crop: "maize" or null
species: "cattle", "goats", "sheep", "camels" or null
rain_reported: true if the sender says it rained or is raining, otherwise false
moisture_pct: number or null (grain moisture percent, e.g. "13%")
language: "sw" or "en" (the main language of the reply)
confidence: 0 to 1, how sure you are of report_type and count

Guide: magunia/gunia = bags; mabichi/unyevu = wet; nimevuna = I harvested; ng'ombe = cattle;
mbuzi = goats; kondoo = sheep; ngamia = camels; wagonjwa = sick; wamekufa = dead;
mafuriko = flooding; kisima/bwawa kimekauka = water point dry; kutupa mimba = abortions (rvf_signs).
If the reply is a greeting, a question or unclear, use report_type "other" with low confidence.

Examples:
"Nimevuna magunia 20, mvua imenyesha, mahindi yana unyevu" ->
{"report_type":"wet_bags","count":20,"unit":"bags","crop":"maize","species":null,"rain_reported":true,"moisture_pct":null,"language":"sw","confidence":0.92}
"Dried 120 bags, 13%" ->
{"report_type":"bags_dried","count":120,"unit":"bags","crop":"maize","species":null,"rain_reported":false,"moisture_pct":13,"language":"en","confidence":0.9}
"Mbuzi watatu wamekufa leo" ->
{"report_type":"dead_animals","count":3,"unit":"animals","crop":null,"species":"goats","rain_reported":false,"moisture_pct":null,"language":"sw","confidence":0.9}"""


class GenAIError(RuntimeError):
    pass


_client = None
_client_lock = threading.Lock()


def _build_client():
    try:
        import oci
    except ImportError as exc:
        raise GenAIError("the 'oci' package is not installed") from exc
    endpoint = settings.NGAO_OCI_GENAI_ENDPOINT or (
        f"https://inference.generativeai.{settings.NGAO_OCI_REGION}.oci.oraclecloud.com"
    )
    kwargs = {
        "service_endpoint": endpoint,
        "retry_strategy": oci.retry.NoneRetryStrategy(),
        "timeout": (3, settings.NGAO_OCI_GENAI_TIMEOUT),
    }
    auth = settings.NGAO_OCI_AUTH
    if auth == "instance_principal":
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        return oci.generative_ai_inference.GenerativeAiInferenceClient({}, signer=signer, **kwargs)
    if auth == "resource_principal":
        signer = oci.auth.signers.get_resource_principals_signer()
        return oci.generative_ai_inference.GenerativeAiInferenceClient({}, signer=signer, **kwargs)
    config = oci.config.from_file(settings.NGAO_OCI_CONFIG_FILE, settings.NGAO_OCI_PROFILE)
    return oci.generative_ai_inference.GenerativeAiInferenceClient(config, **kwargs)


def client():
    global _client
    with _client_lock:
        if _client is None:
            _client = _build_client()
        return _client


def _chat_request(text):
    from oci.generative_ai_inference import models as m

    model_id = settings.NGAO_OCI_GENAI_MODEL_ID
    user_text = f'Reply: "{text}"\nJSON:'
    if model_id.startswith("cohere."):
        return m.CohereChatRequest(
            message=user_text, preamble_override=PREAMBLE, max_tokens=250, temperature=0,
            response_format=m.CohereResponseJsonFormat(schema=JSON_SCHEMA),
        )
    return m.GenericChatRequest(
        messages=[
            m.SystemMessage(content=[m.TextContent(text=PREAMBLE)]),
            m.UserMessage(content=[m.TextContent(text=user_text)]),
        ],
        max_tokens=250, temperature=0,
        response_format=m.JsonSchemaResponseFormat(
            json_schema=m.ResponseJsonSchema(name="ngao_report", schema=JSON_SCHEMA, is_strict=True)
        ),
    )


def _response_text(chat_response):
    text = getattr(chat_response, "text", None)
    if text is None and getattr(chat_response, "choices", None):
        content = chat_response.choices[0].message.content
        text = "".join(getattr(part, "text", "") for part in content or [])
    if not text:
        raise GenAIError("empty response")
    return text


def parse_json_object(text):
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise GenAIError(f"no JSON object in model output: {text[:120]!r}") from None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise GenAIError(f"invalid JSON from model: {exc}") from exc


def extract(text):
    """Return the model's JSON (not yet validated). Raises GenAIError on any failure."""
    if not settings.NGAO_OCI_COMPARTMENT_ID:
        raise GenAIError("OCI_COMPARTMENT_ID is not set")
    from oci.generative_ai_inference import models as m

    details = m.ChatDetails(
        compartment_id=settings.NGAO_OCI_COMPARTMENT_ID,
        serving_mode=m.OnDemandServingMode(model_id=settings.NGAO_OCI_GENAI_MODEL_ID),
        chat_request=_chat_request(text),
    )
    try:
        response = client().chat(details)
    except GenAIError:
        raise
    except Exception as exc:  # oci.exceptions.ServiceError, timeouts, auth problems
        raise GenAIError(f"OCI GenAI call failed: {exc}") from exc
    return parse_json_object(_response_text(response.data.chat_response))
