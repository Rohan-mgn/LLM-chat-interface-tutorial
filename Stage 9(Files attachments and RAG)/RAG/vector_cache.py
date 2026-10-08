"""Bounded packed-vector LRU; generation checks belong to the database caller."""
from array import array
from collections import OrderedDict
from threading import RLock
import json
import math
import os
import sys

def memory(value):
    if isinstance(value,dict): return sys.getsizeof(value)+sum(memory(k)+memory(v) for k,v in value.items())
    if isinstance(value,tuple): return sys.getsizeof(value)+sum(memory(v) for v in value)
    return sys.getsizeof(value)

class VectorCache:
    def __init__(self, limit=None):
        self.limit=max(0,int(os.getenv("RAG_VECTOR_CACHE_MIB","32")))*1024**2 if limit is None else limit
        self.entries=OrderedDict()
        self.lock=RLock()
        self.hits=self.misses=0

    def bytes(self):
        return memory(self.entries)

    def get(self,key):
        with self.lock:
            value=self.entries.get(key)
            if value is None:self.misses+=1
            else:self.hits+=1;self.entries.move_to_end(key)
            return value

    def put(self,key,vectors):
        with self.lock:
            self.invalidate(key[0],key[1],keep=key)
            if not self.limit or memory(vectors)+memory(key)>self.limit:return
            self.entries[key]=vectors;self.entries.move_to_end(key)
            while self.entries and self.bytes()>self.limit:self.entries.popitem(last=False)

    def invalidate(self,cid=None,fid=None,keep=None):
        with self.lock:
            for key in list(self.entries):
                if key!=keep and (cid is None or key[0]==cid) and (fid is None or key[1]==fid):
                    del self.entries[key]

    @staticmethod
    def decode(rows):
        result={}
        for row in rows:
            values=json.loads(row["vector"])
            if not values or not all(type(x) in (int,float) and math.isfinite(x) for x in values):
                raise ValueError("Invalid vector in stored index; re-index.")
            norm=math.sqrt(sum(x*x for x in values))
            if not norm:raise ValueError("Zero vector in stored index; re-index.")
            result[row["id"]]=array("f",(x/norm for x in values))
        return result
