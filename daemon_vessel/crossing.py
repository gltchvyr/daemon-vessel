"""H1 local-only crossing ledger; it never connects a room or mutates canon."""
from __future__ import annotations
import datetime as dt, hashlib, json, os, pathlib, re, tempfile
from dataclasses import dataclass
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

SHA256=re.compile(r"^[0-9a-f]{64}$"); ROOT=pathlib.Path(__file__).resolve().parent.parent
SCHEMAS=pathlib.Path(__file__).resolve().parent/"schemas"; CHECKER=FormatChecker()
class CrossingError(RuntimeError): """Deliberate sanitized H1 refusal."""
@dataclass(frozen=True)
class Paths:
    home:pathlib.Path
    def month(self, kind:str, when:dt.datetime)->pathlib.Path:return self.home/kind/f"{when:%Y}"/f"{when:%m}"
def _now(): return dt.datetime.now(dt.timezone.utc)
def _bytes(value:object)->bytes:return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n").encode()
def _hash(value:bytes)->str:return hashlib.sha256(value).hexdigest()
def _safe(value:str)->bool:
    path=pathlib.PurePath(value);return bool(value) and not path.is_absolute() and ".." not in path.parts and not re.match(r"^[A-Za-z]:",value) and "://" not in value
def resolve_home(home:str|pathlib.Path|None)->Paths:
    selected=home if home is not None else os.environ.get("GLITCH_CONTINUITY_HOME")
    if not selected: raise CrossingError("refused: GLITCH_CONTINUITY_HOME or --home is required")
    path=pathlib.Path(selected).expanduser().resolve(strict=False)
    if path==ROOT or ROOT in path.parents:raise CrossingError("refused: continuity home cannot be inside the repository")
    if any((p/".git").exists() for p in (path,*path.parents)):raise CrossingError("refused: continuity home cannot be within a Git worktree")
    return Paths(path)
def _validator(name:str):
    try:
        schema=json.loads((SCHEMAS/name).read_text(encoding="utf-8"));Draft202012Validator.check_schema(schema)
        refs=Registry().with_resource(schema["$id"],Resource.from_contents(schema,default_specification=DRAFT202012))
        return Draft202012Validator(schema,registry=refs,format_checker=CHECKER)
    except (OSError,json.JSONDecodeError,SchemaError,Unresolvable) as exc:raise CrossingError("refused: invalid or externally referenced crossing schema") from exc
def _validate(value:object,name:str)->dict[str,Any]:
    errors=list(_validator(name).iter_errors(value))
    if errors:
        error=errors[0];path="$"
        for part in error.absolute_path:path+=f"[{part}]" if isinstance(part,int) else f".{part}" if isinstance(part,str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",part) else ".<property>"
        raise CrossingError(f"refused: validation failed at {path} ({error.validator})")
    if not isinstance(value,dict):raise CrossingError("refused: document root must be an object")
    return value
def validate_event(value:object)->dict[str,Any]:
    event=_validate(value,"house-event.schema.json");content=event["content"];transport=event["transport"]
    if content["fidelity"]=="exact_bytes_verified" and(not transport["raw_bytes_available"] or not SHA256.fullmatch(content.get("sha256") or "")):raise CrossingError("refused: exact bytes require raw bytes and a SHA-256 digest")
    if content["fidelity"]=="rendered_content" and transport["raw_bytes_available"]:raise CrossingError("refused: rendered content cannot claim raw bytes")
    if event["continuity"].get("mutation_authorized") is True:raise CrossingError("refused: H1 has no canonical-mutation authority")
    if (content.get("inline") is None)==(content.get("ref") is None):raise CrossingError("refused: content must use exactly one of inline or ref")
    for entries in event["lineage"].values():
        if event["event_id"] in entries or len(entries)!=len(set(entries)):raise CrossingError("refused: invalid event lineage")
    for item in event.get("attachments",[]):
        if item.get("locator") and not _safe(item["locator"]):raise CrossingError("refused: unsafe attachment locator")
    return event
def validate_packet(value:object)->dict[str,Any]:
    packet=_validate(value,"context-packet.schema.json")
    if packet.get("mutation_authorized") is True:raise CrossingError("refused: H1 has no canonical-mutation authority")
    return packet
def _atomic(path:pathlib.Path,data:bytes)->None:
    path.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix="."+path.name+".",dir=path.parent);temp=pathlib.Path(tmp)
    try:
        with os.fdopen(fd,"wb") as out:out.write(data);out.flush();os.fsync(out.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)
def _read(path:pathlib.Path)->list[dict]:
    if not path.exists():return []
    try:return [json.loads(line.decode("utf-8")) for line in path.read_bytes().splitlines()]
    except (UnicodeError,json.JSONDecodeError) as exc:raise CrossingError("refused: ledger has a truncated or invalid tail") from exc
def _append(path:pathlib.Path,data:bytes)->None:
    path.parent.mkdir(parents=True,exist_ok=True);lock=path.with_suffix(path.suffix+".lock")
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError as exc:raise CrossingError("refused: ledger is busy") from exc
    try:
        os.close(fd)
        with path.open("ab") as out:out.write(data);out.flush();os.fsync(out.fileno())
    finally:lock.unlink(missing_ok=True)
def _key(event:dict)->str:
    source=event["source"]
    if source.get("surface_id") and source.get("native_event_id"):return "native:"+source["surface_id"]+":"+source["native_event_id"]
    return "fallback:"+_hash("|".join((event["content"].get("sha256") or "",event["actor"]["actor_id"],source["room_id"],event["occurred_at"][:13])).encode())
def _receipt(event:dict|None,when:dt.datetime,outcome:str,duplicate:bool=False,code:str|None=None)->dict:
    ident="receipt:"+_hash(f"{when.isoformat()}:{outcome}:{event and event.get('event_id')}".encode())[:32]
    return {"schema":"gltch.crossing-receipt/0.1.0","receipt_id":ident,"created_at":when.isoformat(),"operation":"ingest","outcome":outcome,"event_id":event and event.get("event_id"),"content_sha256":event and event.get("content",{}).get("sha256"),"transport":event and event.get("transport"),"idempotent_duplicate":duplicate,"dead_letter":outcome=="refused","failure_code":code,"artifact_paths":[]}
def ingest(value:object,*,home:str|pathlib.Path|None=None)->dict:
    when=_now();paths=resolve_home(home)
    try:
        event=validate_event(value);ledger=paths.month("crossings/events",when)/"events.jsonl";key=_key(event)
        for prior in _read(ledger):
            if prior.get("_source_key")==key:
                if prior.get("event_id")==event["event_id"] and prior.get("content")==event["content"]:
                    receipt=_receipt(event,when,"duplicate",True);rel=paths.month("crossings/receipts",when).relative_to(paths.home)/(receipt["receipt_id"].replace(":","-")+".json");receipt["artifact_paths"]=[str(rel)];_atomic(paths.home/rel,_bytes(receipt));return receipt
                raise CrossingError("refused: divergent source-key collision")
        record=dict(event);record["_source_key"]=key;_append(ledger,_bytes(record))
        receipt=_receipt(event,when,"success");rel=paths.month("crossings/receipts",when).relative_to(paths.home)/(receipt["receipt_id"].replace(":","-")+".json");receipt["artifact_paths"]=[str(ledger.relative_to(paths.home)),str(rel)];_atomic(paths.home/rel,_bytes(receipt));return receipt
    except CrossingError:
        receipt=_receipt(value if isinstance(value,dict) else None,when,"refused",code="validation_or_write_refused");rel=paths.month("crossings/dead-letter",when).relative_to(paths.home)/(receipt["receipt_id"].replace(":","-")+".json");receipt["artifact_paths"]=[str(rel)];_atomic(paths.home/rel,_bytes(receipt));raise
def verify(*,home:str|pathlib.Path|None=None)->dict[str,int]:
    paths=resolve_home(home);events=receipts=0
    for file in (paths.home/"crossings/events").glob("*/*/*.jsonl") if (paths.home/"crossings/events").exists() else []:events+=len(_read(file))
    for file in (paths.home/"crossings/receipts").glob("*/*/*.json") if (paths.home/"crossings/receipts").exists() else []:_validate(json.loads(file.read_text(encoding="utf-8")),"crossing-receipt.schema.json");receipts+=1
    return {"events":events,"receipts":receipts}
