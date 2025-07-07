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


class Translation(models.Model):
    class Meta:
        db_table = "phrase_anchors"

    conjugated = models.BooleanField(null=True, blank=True)
    english = models.TextField(blank=True, null=True)
    phrase_ref = models.IntegerField()


class Phrase(ArangoModel):
    collection_name = "Phrases"

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
