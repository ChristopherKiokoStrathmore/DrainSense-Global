"""Write the Postman collection + environments (and the OpenAPI schema) into backend/postman/."""
import json
from pathlib import Path

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.renderers import OpenApiJsonRenderer

from api.postman import build_collection, build_environment, capture_live


def openapi_dict():
    schema = SchemaGenerator().get_schema(request=None, public=True)
    # Round-trip through the renderer so lazy strings and OrderedDicts become plain JSON.
    return json.loads(OpenApiJsonRenderer().render(schema, renderer_context={}))


class Command(BaseCommand):
    help = "Generate backend/postman/*.json and backend/postman/openapi.yaml from the live OpenAPI schema."

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(Path(settings.BASE_DIR) / "postman"))
        parser.add_argument("--capture-from", metavar="BASE_URL",
                            help="run the walkthrough against this server (e.g. http://127.0.0.1:8000/ngao/v1) "
                                 "and save the real responses as examples")
        parser.add_argument("--api-token", default="change-me-apex")
        parser.add_argument("--ussd-token", default="change-me-ussd")
        parser.add_argument("--webhook-secret", default="")

    def handle(self, *args, **options):
        out = Path(options["out"])
        out.mkdir(parents=True, exist_ok=True)
        schema = openapi_dict()
        collection = build_collection(schema)
        if options["capture_from"]:
            results = capture_live(collection, options["capture_from"], {
                "api_token": options["api_token"], "ussd_token": options["ussd_token"],
                "webhook_secret": options["webhook_secret"]})
            for name, code in results:
                self.stdout.write(f"  {code}  {name}")
        files = {
            "ngao-backend.postman_collection.json": collection,
            "ngao-local.postman_environment.json": build_environment("local", "http://127.0.0.1:8000/ngao/v1"),
            "ngao-oci.postman_environment.json": build_environment("OCI VM", "https://<vm-host>/ngao/v1"),
        }
        for name, data in files.items():
            (out / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        (out / "openapi.yaml").write_text(yaml.safe_dump(schema, sort_keys=False, allow_unicode=True, width=120))
        count = sum(len(folder["item"]) for folder in collection["item"])
        self.stdout.write(self.style.SUCCESS(f"{count} requests in {len(collection['item'])} folders -> {out}"))
