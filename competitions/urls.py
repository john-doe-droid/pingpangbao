"""competitions 路由。"""
from django.urls import path

from . import views

app_name = "competitions"

urlpatterns = [
    path("", views.competition_list, name="list"),
    path("new/", views.competition_create, name="create"),
    path("<int:pk>/", views.competition_detail, name="detail"),
    path("<int:pk>/edit/", views.competition_update, name="update"),
    path("<int:pk>/activate/", views.competition_activate, name="activate"),
    path("<int:pk>/archive/", views.competition_archive, name="archive"),
    path("<int:pk>/reviewers/", views.reviewer_manage, name="reviewers"),
    path(
        "<int:pk>/reviewers/<int:assignment_id>/revoke/",
        views.reviewer_revoke, name="reviewer_revoke",
    ),
]
