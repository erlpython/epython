import django
from django.conf import settings
from django.core.wsgi import get_wsgi_application
from django.http import HttpResponse, JsonResponse
from django.template import Context, Engine
from django.urls import path

settings.configure(
    DEBUG=False,
    SECRET_KEY="bench-only-key",
    ALLOWED_HOSTS=["*"],
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[
        "django.middleware.common.CommonMiddleware",
    ],
)
django.setup()

ENGINE = Engine()
TEMPLATE = ENGINE.from_string(
    "<ul>{% for item in items %}<li>{{ item.id }}: {{ item.name }}</li>{% endfor %}</ul>"
)
SAMPLE_ITEMS = [{"id": i, "name": f"item-{i}"} for i in range(50)]


def json_view(request):
    return JsonResponse({"status": "ok", "items": SAMPLE_ITEMS[:10]})


def template_view(request):
    html = TEMPLATE.render(Context({"items": SAMPLE_ITEMS}))
    return HttpResponse(html)


urlpatterns = [
    path("json/", json_view),
    path("template/", template_view),
]

application = get_wsgi_application()
