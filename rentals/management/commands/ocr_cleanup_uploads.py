from django.core.management.base import BaseCommand

from rentals.ocr.cleanup import cleanup_uploads


class Command(BaseCommand):
    help = "Delete expired OCR uploads from MEDIA_ROOT/ocr_uploads."

    def add_arguments(self, parser):
        parser.add_argument(
            "--ttl-hours",
            type=int,
            default=None,
            help="Override OCR_UPLOAD_TTL_HOURS setting.",
        )

    def handle(self, *args, **options):
        ttl = options.get("ttl_hours")
        result = cleanup_uploads(ttl_hours=ttl)
        self.stdout.write(
            self.style.SUCCESS(
                f"OCR cleanup complete. Scanned: {result['scanned']}, Deleted: {result['deleted']}"
            )
        )
