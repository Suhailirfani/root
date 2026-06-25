const CACHE_NAME = 'root-tuition-v2';
const urlsToCache = [
  '/',
  '/static/images/logo-removebg.png'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        return cache.addAll(urlsToCache);
      })
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});

self.addEventListener('fetch', event => {
  // Use Network First strategy for all requests to ensure fresh dynamic data
  event.respondWith(
    fetch(event.request)
      .then(response => {
        // Optional: Update cache with new response if you want offline support
        return response;
      })
      .catch(() => {
        // If network fails, fallback to cache
        return caches.match(event.request);
      })
  );
});
