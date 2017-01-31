# Makefile for Lily development
# Tabs for MacOS compatibility

default: run

build:
	@docker-compose -f docker-compose.yml -f docker-compose.new-build.yml build
	@gulp clean
	@NODE_ENV=dev gulp build

pull:
	@docker-compose pull

makemigrations:
	@docker-compose run --rm web python manage.py makemigrations

migrate:
	@docker-compose run --rm web python manage.py migrate

index:
	@docker-compose run --rm web python manage.py index -f

testdata:
	@docker-compose run --rm web python manage.py testdata

run:
	@docker-compose run --rm --service-ports web & gulp watch

up:
	@docker-compose up & gulp watch

down:
	@docker-compose down

setup: build migrate index testdata run

.PHONY: default build pull makemigrations migrate index testdata run down setup up
