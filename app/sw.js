/* Pool Pilot service worker: cache the shell, never cache the pool data.
   Readings are fetched live from the private repo (or served from localStorage
   when offline), so they never land in the HTTP cache. */
const SHELL = "poolpilot-shell-2026-09-24-2153";
const FILES = ["./", "./index.html", "./manifest.webmanifest", "./icon-192.png", "./icon-512.png"];

self.addEventListener("install", function(e){
  self.skipWaiting();
  e.waitUntil(caches.open(SHELL).then(function(c){ return c.addAll(FILES); }).catch(function(){}));
});
self.addEventListener("activate", function(e){
  e.waitUntil(caches.keys().then(function(keys){
    return Promise.all(keys.filter(function(k){ return k !== SHELL; })
                           .map(function(k){ return caches.delete(k); }));
  }).then(function(){ return self.clients.claim(); }));
});
self.addEventListener("fetch", function(e){
  const url = new URL(e.request.url);
  if(url.hostname.indexOf("github") >= 0 || url.hostname === "127.0.0.1") return;  // always live
  // On a public single-repo setup the pool data is same-origin, so it would
  // otherwise land in the shell cache and the app would show one stale reading
  // forever. The shell is cacheable; the data never is.
  if(url.pathname.indexOf("/data/store/") >= 0) return;
  if(e.request.method !== "GET") return;
  e.respondWith(
    fetch(e.request).then(function(r){
      const copy = r.clone();
      caches.open(SHELL).then(function(c){ c.put(e.request, copy); }).catch(function(){});
      return r;
    }).catch(function(){ return caches.match(e.request).then(function(m){
      return m || caches.match("./index.html"); }); })
  );
});
