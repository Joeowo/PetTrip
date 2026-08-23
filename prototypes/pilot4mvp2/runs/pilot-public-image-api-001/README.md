# Public image API evidence

This directory records one real image-generation Run executed through the deployed public HTTPS
Agent API. The Run reached `succeeded`, returned one `agent_generated` PNG, and allowed two
authenticated downloads whose SHA-256 values match the API metadata.

The evidence confirms that the deployed service reads the unified `IMAGES_BASE_URL`,
`IMAGES_API_KEY`, and `IMAGES_MODEL` Provider configuration. It does not include the public Base
URL, PetTrip API Key, Provider URL, Provider Key, Provider Base64 response, generated image bytes,
or a server file path.
