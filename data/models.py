from django.db import models
from roma.models import ArangoModel

# Create your models here.
class Sample(ArangoModel):
    collection_name = 'Samples'
    
    def _str_(self):
        return self.sample_ref

class Category(ArangoModel):
    collection_name = 'Categories'

    def _str_(self):
        return self.name

# class Source(models.Model):
#     class Meta:
#         db_table = 'sample_rmsq_sources' # or 'sample_external_source'
#         verbose_name_plural = 'Sources'
    
#     source_id = models.AutoField(primary_key=True)
#     fieldworker = models.CharField(max_length=100, blank=True, null=True,)
#     place = models.CharField(max_length=100, blank=True, null=True,)
#     no_recordings = models.CharField(max_length=3, blank=True, null=True,)
#     orig_format = models.CharField(max_length=50, blank=True, null=True,)
#     # date_received = models.CharField(max_length=20, blank=True, null=True,) # external only
#     date_recieved = models.CharField(max_length=20, blank=True, null=True,) # rmsq only
#     orig_label_content = models.TextField(blank=True, null=True,)
#     recording_quality = models.CharField(max_length=20, blank=True, null=True,)
#     ethno_info = models.CharField(max_length=10, blank=True, null=True,)
#     narative = models.CharField(max_length=10, blank=True, null=True,)
#     comments = models.TextField(blank=True, null=True,)
#     speaker_name = models.CharField(max_length=50, blank=True, null=True,)
#     speaker_age = models.CharField(max_length=20, blank=True, null=True,)
#     # speaker_birth = models.CharField(max_length=50, blank=True, null=True,) # external only
#     speaker_occupation = models.CharField(max_length=20, blank=True, null=True,)
#     speaker_foreign_languages = models.TextField(blank=True, null=True,)
#     speaker_comments = models.TextField(blank=True, null=True,)
#     transcription_avail = models.CharField(max_length=10, blank=True, null=True,) # rmsq only
#     ethno_trans_avail = models.CharField(max_length=10, blank=True, null=True,) # rmsq only
#     narative_trans_avail = models.CharField(max_length=10, blank=True, null=True,) # rmsq only
#     transcription_by = models.CharField(max_length=50, blank=True, null=True,) # rmsq only
#     trans_cmments = models.TextField(blank=True, null=True,) # rmsq only
#     rms_input = models.CharField(max_length=10, blank=True, null=True,) # rmsq only
#     rms_inputted_by = models.CharField(max_length=50, blank=True, null=True,) # rmsq only
#     batch_no = models.CharField(max_length=3, blank=True, null=True,) # rmsq only
#     cleaned_up = models.CharField(max_length=10, blank=True, null=True,) # rmsq only
#     cut_up = models.CharField(max_length=10, blank=True, null=True,) # rmsq only
#     double_checked = models.CharField(max_length=10, blank=True, null=True,) # rmsq only
#     sample = models.CharField(max_length=20, blank=True, null=True,)
#     sound_separated = models.CharField(max_length=20, blank=True, null=True,)
#     in_romani = models.CharField(max_length=20, blank=True, null=True,)


class Translation(models.Model):
    class Meta:
        db_table = 'phrase_anchors'
    
    conjugated = models.BooleanField(null=True, blank=True)
    english = models.TextField(blank=True, null=True)
    phrase_ref = models.IntegerField()
   
# class Phrase(models.Model):
#     class Meta:
#         db_table = 'sample_phrases'

#     sample_ref = models.CharField(max_length=100, db_column='sample', blank=True, null=True)
#     phrase = models.CharField(max_length=225)
#     translation = models.ForeignKey(Translation, on_delete=models.CASCADE, db_column='phrase_anchor')
    
class Phrase(ArangoModel):
    collection_name = 'Phrases'

    def _str_(self):
        return self.phrase
    

class Source(ArangoModel):
    collection_name = 'Sources'

    def _str_(self):
        return self.sample + (f" {self.place}" if self.place else '')
    
class Answer(ArangoModel):
    collection_name = 'Answers'

    def _str_(self):
        return f"An answer for {self.sample}"

class View(ArangoModel):
    collection_name = 'Views'

    def __str__(self):
        return self.filename

class Transcription(ArangoModel):
    collection_name = 'Transcriptions'

    def __str__(self):
        return f"Transcription for {self.sample} segment {self.segment_no}"
