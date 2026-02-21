# models.py
from tortoise.models import Model
from tortoise import fields


class Gateway(Model):
    id = fields.IntField(pk=True)
    mac_address = fields.CharField(max_length=17, unique=True)
    hostname = fields.CharField(max_length=255)
    ip_address = fields.CharField(max_length=39)
    name = fields.CharField(max_length=100)
    description = fields.TextField()
    access_token = fields.CharField(max_length=64)
    last_heartbeat = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField()
    updated_at = fields.DatetimeField()

    class Meta:
        table = "gateway"

    def __repr__(self):
        return f"<Gateway(id={self.id}, name={self.name})>"
