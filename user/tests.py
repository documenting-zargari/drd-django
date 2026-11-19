from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from user.export import encrypt, fingerprint, import_encrypted, users_fixture
from user.models import CustomUser, UserProjectRole
from user.serializers import UserWriteSerializer


class ShowHiddenSamplesModelTests(TestCase):
    def test_default_is_false(self):
        user = CustomUser.objects.create_user(username="u", password="pw")
        self.assertFalse(user.show_hidden_samples)

    def test_can_be_set_on_global_admin(self):
        user = CustomUser.objects.create_user(
            username="admin", password="pw", is_global_admin=True, show_hidden_samples=True
        )
        self.assertTrue(user.show_hidden_samples)


class ShowHiddenSamplesSerializerTests(TestCase):
    def test_rejects_enable_for_non_admin(self):
        data = {
            "username": "editor",
            "password": "password123",
            "is_global_admin": False,
            "show_hidden_samples": True,
        }
        serializer = UserWriteSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("show_hidden_samples", serializer.errors)

    def test_accepts_enable_for_global_admin(self):
        data = {
            "username": "admin",
            "password": "password123",
            "is_global_admin": True,
            "show_hidden_samples": True,
        }
        serializer = UserWriteSerializer(data=data, context={"admin_projects": None})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save()
        self.assertTrue(user.show_hidden_samples)

    def test_resets_flag_when_demoting_from_admin(self):
        user = CustomUser.objects.create_user(
            username="admin", password="pw", is_global_admin=True, show_hidden_samples=True
        )
        serializer = UserWriteSerializer(
            instance=user,
            data={"is_global_admin": False},
            partial=True,
            context={"admin_projects": None},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        updated = serializer.save()
        self.assertFalse(updated.is_global_admin)
        self.assertFalse(updated.show_hidden_samples)


class UserSeesHiddenSamplesHelperTests(TestCase):
    def setUp(self):
        from data.views import user_sees_hidden_samples
        self.helper = user_sees_hidden_samples

    def test_anonymous_false(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(self.helper(AnonymousUser()))

    def test_none_false(self):
        self.assertFalse(self.helper(None))

    def test_regular_user_false(self):
        user = CustomUser.objects.create_user(username="u", password="pw")
        self.assertFalse(self.helper(user))

    def test_global_admin_without_flag_false(self):
        user = CustomUser.objects.create_user(
            username="admin", password="pw", is_global_admin=True, show_hidden_samples=False
        )
        self.assertFalse(self.helper(user))

    def test_project_admin_with_flag_false(self):
        # Flag is ignored unless user is global admin
        user = CustomUser.objects.create_user(
            username="padmin", password="pw", is_global_admin=False, show_hidden_samples=True
        )
        UserProjectRole.objects.create(user=user, project="rms", role="admin")
        self.assertFalse(self.helper(user))

    def test_global_admin_with_flag_true(self):
        user = CustomUser.objects.create_user(
            username="admin", password="pw", is_global_admin=True, show_hidden_samples=True
        )
        self.assertTrue(self.helper(user))


class ShowHiddenSamplesAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = CustomUser.objects.create_user(
            username="admin", password="pw", is_global_admin=True
        )
        self.editor = CustomUser.objects.create_user(
            username="editor", password="pw", is_global_admin=False
        )

    def test_me_endpoint_exposes_flag(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/users/me/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("show_hidden_samples", resp.data)
        self.assertFalse(resp.data["show_hidden_samples"])

    def test_admin_can_toggle_own_flag(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(
            f"/users/{self.admin.id}/",
            {"show_hidden_samples": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.show_hidden_samples)

    def test_non_admin_cannot_enable_flag(self):
        self.client.force_authenticate(user=self.editor)
        resp = self.client.patch(
            f"/users/{self.editor.id}/",
            {"show_hidden_samples": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.editor.refresh_from_db()
        self.assertFalse(self.editor.show_hidden_samples)


class SuperuserIsGlobalAdminTests(TestCase):
    def test_createsuperuser_sets_global_admin(self):
        user = CustomUser.objects.create_superuser(username="root", password="pw")
        self.assertTrue(user.is_global_admin)

    def test_regular_user_is_not_global_admin(self):
        user = CustomUser.objects.create_user(username="u", password="pw")
        self.assertFalse(user.is_global_admin)


TEST_FERNET = Fernet(Fernet.generate_key())


@override_settings(FIELD_FERNET=TEST_FERNET)
class UserExportTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(username="editor1", password="pw", email="e@example.org")
        UserProjectRole.objects.create(user=self.user, project="rms", role="editor", allowed_samples="TR-001")

    def test_roundtrip_restores_users_and_roles(self):
        token = encrypt(users_fixture())
        CustomUser.objects.all().delete()
        import_encrypted(token)
        user = CustomUser.objects.get(username="editor1")
        self.assertTrue(user.check_password("pw"))
        self.assertEqual(user.project_roles.get().sample_list, ["TR-001"])

    def test_fingerprint_ignores_last_login(self):
        before = fingerprint(users_fixture())
        self.user.last_login = timezone.now()
        self.user.save()
        self.assertEqual(before, fingerprint(users_fixture()))

    def test_endpoint_returns_ciphertext_only(self):
        response = APIClient().get("/export/users/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"editor1", response.content)
        self.assertIn(b"editor1", TEST_FERNET.decrypt(response.content))
        self.assertEqual(response["X-Content-Fingerprint"], fingerprint(users_fixture()))

    @override_settings(FIELD_FERNET=None)
    def test_endpoint_unavailable_without_key(self):
        self.assertEqual(APIClient().get("/export/users/").status_code, 503)
