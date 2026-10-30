"""Fixture-only providers composed beside their production registry owners."""

SETTINGS = {
    "ANGEE_TEST_CONNECTION_CLASSES": {
        "connection": "tests.integrate_fixtures.ConnectionAdapter",
        "live": "tests.integrate_fixtures.LiveConnectionAdapter",
    },
    "ANGEE_VCS_BACKEND_CLASSES.stub": "tests.conftest.StubVCSBackend",
    "ANGEE_INFERENCE_BACKEND_CLASSES.stub_inference": "tests.conftest.StubInferenceBackend",
    "ANGEE_CHANNEL_BACKEND_CLASSES.fake_live": "tests.pairing_backend.FakePairingBackend",
    "ANGEE_POSTS_FEED_BACKEND_CLASSES.stub": "tests.conftest.StubFeedBackend",
    "ANGEE_EXTRACTION_PROFILE_CLASSES.retention_notes": "tests.test_extraction_models.NotesProfile",
    "ANGEE_EXTRACTION_PROFILE_CLASSES.step_text": "tests.test_extraction_steps.TextProfile",
}
