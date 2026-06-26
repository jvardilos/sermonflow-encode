

### Build img

``` bash
docker build -t sermonflow .
```

### Run img

``` bash
docker run \
  -v $(pwd)/in:/app/in \
  -v $(pwd)/output:/app/output \
  sermonflow
```