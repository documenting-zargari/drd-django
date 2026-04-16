from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="show_hidden_samples",
            field=models.BooleanField(
                default=False,
                help_text="Global admin preference: include samples marked as not visible in listings and queries.",
            ),
        ),
    ]
