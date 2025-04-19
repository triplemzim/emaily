from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("verify", views.verify, name="verify"),
    path("progress", views.progress, name="progress"),
    path("log", views.log, name="log"),
    path("cancel", views.cancel, name="cancel"),
    path("download", views.download, name="download"),
]