from django.db import models

from roma.models import ArangoModel


# Create your models here.
class Sample(ArangoModel):
    collection_name = "Samples"

    def _str_(self):
        return self.sample_ref


class Category(ArangoModel):
    collection_name = "Categories"

    def _str_(self):
        return self.name


class ResearchQuestion(ArangoModel):
    collection_name = "ResearchQuestions"

    def _str_(self):
        return self.name


class Translation(models.Model):
    class Meta:
        db_table = "phrase_anchors"

    conjugated = models.BooleanField(null=True, blank=True)
    english = models.TextField(blank=True, null=True)
    phrase_ref = models.IntegerField()


class MasterPhrase(ArangoModel):
    """One doc per distinct elicited phrase, keyed by phrase_ref. Replaces
    the old PhraseAnchors + per-phrase tag_ids. See
    extract/master_phrases_migration/PLAN.md."""

    collection_name = "MasterPhrases"

    def _str_(self):
        return self.english


class SamplePhrase(ArangoModel):
    """One doc per (sample, phrase_ref) recording, keyed by
    '{sample}_{phrase_ref}'. Replaces the old Phrases collection."""

    collection_name = "SamplePhrases"

    def _str_(self):
        return self.phrase


class Source(ArangoModel):
    collection_name = "Sources"

    def _str_(self):
        return self.sample + (f" {self.place}" if self.place else "")


class Answer(ArangoModel):
    collection_name = "Answers"

    def _str_(self):
        return f"An answer for {self.sample}"


class View(ArangoModel):
    collection_name = "Views"

    def __str__(self):
        return self.filename


class Transcription(ArangoModel):
    collection_name = "Transcriptions"

    def __str__(self):
        return f"Transcription for {self.sample} segment {self.segment_no}"
