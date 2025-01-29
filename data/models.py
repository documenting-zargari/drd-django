from django.db import models

# Create your models here.
class Sample(models.Model):
    class Meta:
        db_table = 'samples'
    
    sample_ref = models.CharField(max_length=20, primary_key=True)
    source_type = models.CharField(max_length=50, null=True)
    dialect_group = models.IntegerField(null=True)
    self_attrib_name = models.CharField(max_length=100, null=True)
    dialect_name = models.CharField(max_length=100)
    location = models.CharField(max_length=100, null=True)
    country_code = models.CharField(max_length=5, null=True)
    live = models.BooleanField(null=True)
    longitude = models.CharField(max_length=10, null=True)
    latitude = models.CharField(max_length=10, null=True)
    visible = models.CharField(max_length=3, null=True)
    migrant = models.CharField(max_length=3, null=True)

    def _str_(self):
        return self.sample_ref

class Category(models.Model):
    class Meta:
        db_table = 'categories'
        verbose_name_plural = 'Categories'
    
    category_id = models.AutoField(primary_key=True)
    category_name = models.CharField(max_length=50)
    category_description = models.TextField(blank=True, null=True,)
    category_image = models.CharField(max_length=100, blank=True, null=True)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, blank=True, null=True, db_column='parent_category')
    path = models.CharField(max_length=200, blank=True, null=True)

    def _str_(self):
        return self.category_name

class Source(models.Model):
    class Meta:
        db_table = 'sample_rmsq_sources' # or 'sample_external_source'
        verbose_name_plural = 'Sources'
    
    source_id = models.AutoField(primary_key=True)
    fieldworker = models.CharField(max_length=100, blank=True, null=True,)
    place = models.CharField(max_length=100, blank=True, null=True,)
    no_recordings = models.CharField(max_length=3, blank=True, null=True,)
    orig_format = models.CharField(max_length=50, blank=True, null=True,)
    # date_received = models.CharField(max_length=20, blank=True, null=True,) # external only
    date_recieved = models.CharField(max_length=20, blank=True, null=True,) # rmsq only
    orig_label_content = models.TextField(blank=True, null=True,)
    recording_quality = models.CharField(max_length=20, blank=True, null=True,)
    ethno_info = models.CharField(max_length=10, blank=True, null=True,)
    narative = models.CharField(max_length=10, blank=True, null=True,)
    comments = models.TextField(blank=True, null=True,)
    speaker_name = models.CharField(max_length=50, blank=True, null=True,)
    speaker_age = models.CharField(max_length=20, blank=True, null=True,)
    # speaker_birth = models.CharField(max_length=50, blank=True, null=True,) # external only
    speaker_occupation = models.CharField(max_length=20, blank=True, null=True,)
    speaker_foreign_languages = models.TextField(blank=True, null=True,)
    speaker_comments = models.TextField(blank=True, null=True,)
    transcription_avail = models.CharField(max_length=10, blank=True, null=True,) # rmsq only
    ethno_trans_avail = models.CharField(max_length=10, blank=True, null=True,) # rmsq only
    narative_trans_avail = models.CharField(max_length=10, blank=True, null=True,) # rmsq only
    transcription_by = models.CharField(max_length=50, blank=True, null=True,) # rmsq only
    trans_cmments = models.TextField(blank=True, null=True,) # rmsq only
    rms_input = models.CharField(max_length=10, blank=True, null=True,) # rmsq only
    rms_inputted_by = models.CharField(max_length=50, blank=True, null=True,) # rmsq only
    batch_no = models.CharField(max_length=3, blank=True, null=True,) # rmsq only
    cleaned_up = models.CharField(max_length=10, blank=True, null=True,) # rmsq only
    cut_up = models.CharField(max_length=10, blank=True, null=True,) # rmsq only
    double_checked = models.CharField(max_length=10, blank=True, null=True,) # rmsq only
    sample = models.CharField(max_length=20, blank=True, null=True,)
    sound_separated = models.CharField(max_length=20, blank=True, null=True,)
    in_romani = models.CharField(max_length=20, blank=True, null=True,)


class Translation(models.Model):
    class Meta:
        db_table = 'phrase_anchors'
    
    conjugated = models.BooleanField(null=True, blank=True)
    english = models.TextField(blank=True, null=True)
   
class Phrase(models.Model):
    class Meta:
        db_table = 'sample_phrases'

    sample = models.ForeignKey(Sample, on_delete=models.CASCADE, db_column='sample')
    phrase = models.CharField(max_length=225)
    translation = models.ForeignKey(Translation, on_delete=models.CASCADE, db_column='phrase_anchor')