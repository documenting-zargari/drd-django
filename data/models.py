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

    def get_objects(self):
        return Sample.objects.filter(visible='yes').order_by('sample_ref')

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

    def get_objects(self):
        return Category.objects.all().order_by('category_name')

