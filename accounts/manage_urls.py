from django.urls import path

from . import views

app_name = "members_admin"

urlpatterns = [
    path("", views.managed_members, name="list"),
    path("new/", views.managed_member_create, name="create"),
    path("<int:pk>/reset-password/", views.managed_member_password, name="password"),
    path("<int:pk>/toggle/", views.managed_member_toggle, name="toggle"),
]
