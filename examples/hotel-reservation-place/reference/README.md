# Reference

The reference is DeathStarBench's hotelReservation application — a Go
microservices stack (frontend, search, geo, rate, profile, recommendation,
user, reservation) backed by MongoDB and Memcached, deployed via Docker Compose.

Repo: https://github.com/delimitrou/DeathStarBench/tree/master/hotelReservation

See `meta.json` for the confirmed API contract, seeded credentials, hotel
capacities, and resource partitioning between checker and benchmark.

## Deploy

    git clone https://github.com/delimitrou/DeathStarBench.git
    cd DeathStarBench/hotelReservation
    docker compose up -d

If port 5000 is already in use (common on macOS due to AirPlay Receiver),
either disable AirPlay Receiver in System Settings, or remap the port in
docker-compose.yml and update `--base-url` accordingly.

Wait ~30s, then verify:

    curl "http://localhost:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=37.7749&lon=-122.4194"

## Teardown

    docker compose down -v

The `-v` flag removes MongoDB volumes for a clean next run. Without `-v`,
reservations persist across restarts (there is no /reset endpoint).
