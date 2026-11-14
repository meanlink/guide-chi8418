import hashlib, json
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j, progress=None, segments=None):
    data={k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at']}
    data['segments_total']=j.segments_total
    data['segments_reused']=j.segments_reused
    data['epoch']=j.epoch
    data['lease_owner']=j.lease_owner
    data['lease_expires_at']=j.lease_expires_at
    data['progress']=progress or {'total':j.segments_total,'completed':0,'failed':0,'running':0,'pending':j.segments_total,'percent':0.0}
    data['segments']=segments or []
    return data
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}
