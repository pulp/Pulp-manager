"""Base class Repository services will inherit from
"""

from typing import List
import sqlalchemy
from sqlalchemy import select, and_, insert, update, func
from sqlalchemy.orm import joinedload, Session
from pulp_manager.app.config import CONFIG
from pulp_manager.app.exceptions import PulpManagerFilterError, PulpManagerInvalidPageSize
from pulp_manager.app.models import TaskState, TaskType, RepoHealthStatus


class ITableRepository:
    """Interface that any repository should inherit from for interacting
    with the a db
    """

    __model__ = NotImplemented


    def filter(self, eager: List=None, **kwargs):
        """Returns all entities that match the given kwargs

        :param eager: List of relationships to eagerly load
        :type eager: list
        :param kwargs: kwargs to use for filter the entity
        :type kwargs: dict
        :return: list
        """

        raise NotImplementedError

    def filter_join(self, eager_load: bool, **kwargs):
        """Returns all entities that match the given kwargs. Carries out joins on any
        entities (including nested joins) for remote fields to be queried. Implementation
        is to be done on a per repository basis. Care needs to be taken care with joins
        where foreign key values are null to make sure that results are not accidentally
        ommitted.

        :param eager_load: Eagerly load all the joined tables
        :type eager_load: bool
        :param kwargs: kwargs to used on the filter
        :type kwargs: dict
        :return: list
        """

        raise NotImplementedError

    def count(self):
        """Returns a count of the number of items for the model
        """

        raise NotImplementedError

    def count_filter(self, **kwargs):
        """Retruns the count of the number of entities that match the given
        kwargs

        :param kwargs: dict of arguments to use for filtering
        :type kwargs: dict
        :return: int
        """

        raise NotImplementedError

    def count_filter_join(self, **kwargs):
        """Returns a count of the number of entities that exist in the database for given kwargs.
        To be used in conjunction with filter_join and filter_join_paged

        :param kwargs: dict of arugments to query on
        :type kwargs: dict
        :return: int
        """

        raise NotImplementedError

    def filter_paged(self, page: int=1, page_size: int=50, eager: List=None, **kwargs):
        """Returns entities that match thegiven kwargs, but only returns a subset of the results

        :param page: which page of results should be returned
        :type page: int
        :param page_size: Maximum number of results to return in the page
        :type page_size: int
        :param eager: List of relationships to eagerly load
        :type eager: List
        :param kwargs: kwargs to use of filter results with
        :type kwargs: dict
        :return: list
        """

        raise NotImplementedError

    def filter_join_paged(self, eager_load: bool, page: int=1, page_size: int=50, **kwargs):
        """Returns entities that match the given kwargs. Entities that are returned are offset from
        the given page number and page size. Call filter_join which needs to be implemeted
        on a per repository basis, see filter_join for more details.

        :param eager_load: eagerly load all the joined tables
        :type eager_load: bool
        :param page: page number of results to get. Defaults to 1
        :type page: int
        :param page_size: number of results to return. Defaults to 50
        :type page_size: int
        :param kwargs: kwargs to filter results on
        :type kwargs: dict
        :return: list
        """

        raise NotImplementedError

    def filter_paged_result(self, page: int=1, page_size: int=50, eager: List=None, **kwargs):
        """Returns entities that match the given kwargs. Entities are returned as a list in a dict,
        where the dict also contains the count, page number and page size

        :param page: which page of results should be returned
        :type page: int
        :param page_size: Maximum number of results to return in the page
        :type page_size: int
        :param eager: List of relationships to eagerly load
        :type eager: List
        :param kwargs: kwargs to use of filter results with
        :type kwargs: dict
        :return: dict
        """

        raise NotImplementedError

    def filter_join_paged_result(self, eager_load: bool, page: int=1, page_size: int=50, **kwargs):
        """Returns entries that match the given kwargs. Entities that are returned are offset from
        the given page number and page size. Calls filter_join_page, which calls filter_join, which
        needs to be implemented on a repository by repository basis.

        :param eager_load: eagerly load the joined entities
        :type eager_load: bool
        :param page: page number to load. Defaults to 1
        :type page: int
        :param page_size: number of results to return. Defaults to 50
        :type page_size: int
        :return: dict
        """

        raise NotImplementedError

    def first(self, eager: List=None, **kwargs):
        """Returns the first matching entity for the given kwargs
        
        :param eager: List of relationships to eagerly load
        :type eager: list
        :param kwargs: kwargs to match on
        :type kwargs: dict
        :return: entity or none
        """

        raise NotImplementedError

    #pylint: disable=redefined-builtin
    def get_by_id(self, id: int, eager: List=None):
        """Returns the model with the specified ID or none if there are no matches

        :param id: ID of the entity to find
        :type id: int
        :param eager: List of relationships to eagerly load
        :type eager: list
        :return: Matching model or none if not found
        """

        raise NotImplementedError

    def add(self, **kwargs):
        """Adds a new instnace of the model to the DB but does not commit

        :param kwargs: args to use to create new instance of entity
        :type kwargs: dict
        :reutrn: entity
        """

        raise NotImplementedError

    def bulk_add(self, entities: List):
        """Bulk adds the list of entities to the db and return the new new objects.
        Entities are given as dicts, with the key value pairs needed to create the entity

        :param entities: list of dicts
        :type entities: list
        :return: list
        """

        raise NotImplementedError

    def update(self, entity, **kwargs):
        """Updates existing entity in db but does not commit

        :param entity: entity to update in the db
        :type entity: __model__
        :param kwargs: kwargs to update the model with
        :type kwargs: dict
        """

        raise NotImplementedError

    def bulk_update(self, entities: List):
        """Bulkd updates the given list of entites. List of dicts which must contain the primary
        key so that the entity can be found to update

        :param entities: list of dicts which contain the fields to udpate on models
        :type entities: list
        """

        raise NotImplementedError

    def delete(self, entity):
        """Delets the specified entity from the db

        :param entity: eneity to remove from the database
        :type entity: __model__
        """

        raise NotImplementedError


#pylint: disable=redefined-builtin, not-callable
class TableRepository(ITableRepository):
    """Base class all repositories inherit from

    :var __model__: Database model the repository supports
    :var __remote_filter_name_to_field__: Used to map fields that have been submited in a query
                                          to the remote object and field that needs to be
                                          referneced in a join
    :var __field_remap__: Used to remap names of fields to another attribute of the model.
                          Used as a workaround when querying with enums as enum column
                          type was not used
    """

    __model__ = NotImplemented
    __remote_filter_name_to_field__ = {}
    __field_remap__ = {}

    def __init__(self, db: Session):
        """Constructor

        :param db: session to use connecting to DB
        :type db: AsyncSession
        """

        self.db = db

    # pylint: disable=too-many-branches
    def _build_filter(self, remote_columns_allowed: bool, **kwargs):
        """Returns a list of filters to apply to queries

        :param remote_columns_allowed: Allows for the filter to contain columns/fileds
                                       from remote/foreign objects.
        :param kwargs: kwargs to filter on
        :type kwargs: dict
        :return: list
        """

        filters = []

        for key, value in kwargs.items():
            field_name = key.split("__")[0]

            if field_name in ["order_by", "sort_by"]:
                continue

            if field_name in self.__remote_filter_name_to_field__:
                if not remote_columns_allowed:
                    raise PulpManagerFilterError(
                        "Remote entity columns specified in filter. This is not allowed, "
                        "use filter_join instead"
                    )
                attr = self.__remote_filter_name_to_field__[field_name]
            elif field_name in self.__field_remap__:
                attr = self.__field_remap__[field_name]
            else:
                attr = getattr(self.__model__, field_name)

            # Bit of a work around because not using enums correctly
            if field_name == "state":
                value = TaskState[value].value
            elif field_name == "task_type":
                value = TaskType[value].value
            elif field_name in ["repo_sync_health_rollup", "repo_sync_health"]:
                value = RepoHealthStatus[value].value

            if "__" in key:
                field_name_split = key.split("__")

                if field_name_split[1] == "like":
                    filters.append((attr.like(value)))
                elif field_name_split[1] == "gt":
                    filters.append((attr > value))
                elif field_name_split[1] == "ge":
                    filters.append((attr >= value))
                elif field_name_split[1] == "lt":
                    filters.append((attr < value))
                elif field_name_split[1]  == "le":
                    filters.append((attr <= value))
                elif field_name_split[1] == "in":
                    filters.append((attr.in_(tuple(value.split(",")))))
                elif field_name_split[1] == "match":
                    filters.append(attr.op('regexp')(value))
                else:
                    raise PulpManagerFilterError(f"Unsupported query option {field_name_split[1]}")
            else:
                filters.append((attr == value))

        return filters

    def _get_sort_by_order_by(self, **kwargs):
        """Returns a tuple where first value is sort_by and second value is order_by

        :param kwargs: query kwargs to get order by and sort by from
        :type kwargs: dict
        :return: tuple
        """

        sort_by = None
        order_by = None

        if "sort_by" in kwargs:
            sort_by = kwargs["sort_by"]
            order_by = "asc"
            if "order_by" in kwargs and kwargs["order_by"] == "desc":
                order_by = "desc"

        return sort_by, order_by

    def _apply_query_sorting(self, order_by: str, sort_by: str, remote_columns_allowed: bool,
            query: sqlalchemy.sql.selectable.Select):
        """
        Applies sorting to the given sqlalchemy select query
        
        :param order_by: method to use to sort asc/desc
        :type order_by: str
        :param sort_by: column to sort on
        :type sort_by: str
        :param remote_columns_allowed: Allows for the field to be sorted on to come from a remote
                                       entity. For this to work the appropriate joins need to have
                                       been put in place
        :return: sqlalchemy.sql.selectable.Select
        """

        if sort_by is None:
            return query

        attr = None
        if sort_by in self.__remote_filter_name_to_field__:
            if not remote_columns_allowed:
                raise PulpManagerFilterError(
                    "Remote sort_by field specified in filter. This isn't allowed for a basic "
                    "filter. filter_joined is required instead"
                )
            attr = self.__remote_filter_name_to_field__[sort_by]
        else:
            attr = getattr(self.__model__, sort_by)

        if sort_by and order_by == "asc":
            query = query.order_by(attr)
        else:
            query = query.order_by(attr.desc())

        return query


    def _filter(self, eager: List=None, count: bool=False, **kwargs):
        """Returns base query used for filtering/first. Paging is applied
        by filter method

        :param eager: List of relationships to eagerly load
        :type eager: List
        :param count: Changes the select statement to produce a SELECT count(*)
        :type count: bool
        :param kwargs: kwargs to filter on
        :type kwargs: dict
        :return: sqlalchemy query object
        """

        filters = self._build_filter(False, **kwargs)
        sort_by, order_by = self._get_sort_by_order_by(**kwargs)

        query = select(self.__model__)

        if count:
            query = query.with_only_columns(func.count())

        if not count and eager:
            for relation in eager:
                query = query.options(joinedload(getattr(self.__model__, relation)))

        if len(filters) > 0:
            query = query.where(and_(*filters))

        if not count:
            query = self._apply_query_sorting(order_by, sort_by, False, query)

        return query

    def _get_base_filter_join_query(self, eager_load: bool):
        """Returns the query that contains all realted tables joined for querying

        :param eager_load: eagerly load all the joined table
        :type eager_load: bool
        """

        raise NotImplementedError

    def _filter_join(self, eager_load: bool, count: bool=False, **kwargs):
        """Builds the query that can be used for quering remote entities and columns.

        :param eager_load: eagerly load the joined tables
        :type eager_load: bool
        :param count: generated a SELECT(COUNT(*)) query
        :type count: bool
        :return: sqlalchemy.sql.selectable.Select
        """

        filters = self._build_filter(True, **kwargs)
        sort_by, order_by = self._get_sort_by_order_by(**kwargs)

        query = self._get_base_filter_join_query(eager_load)

        if count:
            query = query.with_only_columns(func.count())

        if len(filters) > 0:
            query = query.where(and_(*filters))

        if not count:
            query = self._apply_query_sorting(order_by, sort_by, True, query)

        return query

    def filter(self, eager: List=None, **kwargs):
        """Returns all entities that match the given kwargs

        :param eager: List of relationships to eagerly load
        :type eager: list
        :param kwargs: kwargs to use for filter the entity
        :type kwargs: dict
        :return: list
        """

        query = self._filter(eager=eager, count=False, **kwargs)
        result = self.db.execute(query)
        # https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html#joined-eager-loading
        if eager:
            return result.scalars().unique().all()
        return result.scalars().all()

    def filter_join(self, eager_load: bool, **kwargs):
        """Returns all entities that match the given kwargs. Carries out joins on entities
        so that remote fields can be queried

        :param eager_load: specifies if joined tables should be eagerly loaded
        :type eager_load: bool
        :param kwargs: kwargs to filter on
        :type kwargs: dict
        """

        query = self._filter_join(eager_load, False, **kwargs)
        result = self.db.execute(query)

        if eager_load:
            return result.scalars().unique().all()
        return result.scalars().all()

    def count(self):
        """Returns a count of the number of items for the model
        """

        result = self.db.execute(select(func.count()).select_from(self.__model__))
        return result.scalars().one()

    def count_filter(self, **kwargs):
        """Retruns the count of the number of entities that match the given
        kwargs

        :param kwargs: dict of arguments to use for filtering
        :type kwargs: dict
        :return: int
        """

        query = self._filter(count=True, **kwargs)
        result = self.db.execute(query)
        return result.scalar_one()

    def count_filter_join(self, **kwargs):
        """Returns a count of the number of entites that exist in the database for given kwargs

        :param kwargs: dict of kwargs top filter on
        :type kwargs: dict
        :return: int
        """

        query = self._filter_join(False, True, **kwargs)
        result = self.db.execute(query)
        return result.scalar_one()

    def filter_paged(self, page: int=1, page_size: int=50, eager: List=None, **kwargs):
        """Returns entities that match thegiven kwargs, but only returns a subset of the results

        :param page: which page of results should be returned
        :type page: int
        :param page_size: Maximum number of results to return in the page
        :type page_size: int
        :param eager: List of relationships to eagerly load
        :type eager: List
        :param kwargs: kwargs to use of filter results with
        :type kwargs: dict
        :return: list
        """

        if int(page_size) > int(CONFIG["paging"]["max_page_size"]):
            raise PulpManagerInvalidPageSize(
                f"page_size {page_size} larger than maximum {CONFIG['paging']['max_page_size']}"
            )

        query = self._filter(eager=eager, count=False, **kwargs)
        query = query.limit(int(page_size)).offset((page - 1) * int(page_size))
        result = self.db.execute(query)
        #https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html#joined-eager-loading
        if eager:
            return result.scalars().unique().all()
        return result.scalars().all()

    def filter_join_paged(self, eager_load: bool, page: int=1, page_size: int=50, **kwargs):
        """Return entities that match the given kwargs. Allows for querying on remote
        fields/columns

        :param eager_load: eagerly load joined tables
        :type eager_load: bool
        :param page: page number to load
        :type page: int
        :param page_size: maximum number of results to return in the page
        :type page_size: int
        :param kwargs: kwargs to use to filter results on
        :type kwargs: dict
        :return: list
        """

        if int(page_size) > int(CONFIG["paging"]["max_page_size"]):
            raise PulpManagerInvalidPageSize(
                f"page_size {page_size} larger than maximum {CONFIG['paging']['max_page_size']}"
            )

        query = self._filter_join(eager_load, False, **kwargs)
        query = query.limit(int(page_size)).offset((page - 1) * int(page_size))
        result = self.db.execute(query)

        #https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html#joined-eager-loading
        if eager_load:
            return result.scalars().unique().all()
        return result.scalars().all()

    def filter_paged_result(self, page: int=1, page_size: int=50, eager: List=None, **kwargs):
        """Returns entities that match the given kwargs. Entities are returned as a list in a dict,
        where the dict also contains the count, page number and page size

        :param page: which page of results should be returned
        :type page: int
        :param page_size: Maximum number of results to return in the page
        :type page_size: int
        :param eager: List of relationships to eagerly load
        :type eager: List
        :param kwargs: kwargs to use of filter results with
        :type kwargs: dict
        :return: dict
        """

        results = self.filter_paged(page, page_size, eager, **kwargs)
        count = 0

        if len(kwargs) > 0:
            # way query is being constructed if there are no kwargs the count
            # comes back incorrectly
            count = self.count_filter(**kwargs)
        else:
            count = self.count()

        return {
            "items": results,
            "page": page,
            "page_size": page_size,
            "total": count
        }

    def filter_join_paged_result(self, eager_load: bool, page: int=1, page_size: int=50, **kwargs):
        """Returns entties that match the given kwargs. Supports querying remote fields/columns.
        Entities are returned as a list in a dict, where the dict also contains the count, page
        number and page size

        :param eager_load: eagerly load joined table
        :type eager_load: bool
        :param page: page of results to return
        :type page: int
        :param  page_size: maximuim number of results to return in the page
        :type page_size: int
        :param kwargs: kwargs to filter on
        :type kwargs: dict
        :return: dict
        """

        results = self.filter_join_paged(eager_load, page, page_size, **kwargs)
        count = self.count_filter_join(**kwargs)

        return {
            "items": results,
            "page": page,
            "page_size": page_size,
            "total": count
        }

    def first(self, eager: List=None, **kwargs):
        """Returns the first matching entity for the given kwargs
        
        :param eager: List of relationships to eagerly load
        :type eager: list
        :param kwargs: kwargs to match on
        :type kwargs: dict
        :return: entity or none
        """

        result = self.filter(eager=eager, **kwargs)
        if len(result) == 0:
            return None
        return result[0]

    def get_by_id(self, id: int, eager: List=None):
        """Returns the model with the specified ID or none if there are no matches

        :param id: ID of the entity to find
        :type id: int
        :param eager: List of relationships to eagerly load
        :type eager: list
        :return: Matching model or none if not found
        """

        result = self.first(id=id, eager=eager)
        return result

    def add(self, **kwargs):
        """Adds a new instnace of the model to the DB but does not commit

        :param kwargs: args to use to create new instance of entity
        :type kwargs: dict
        :reutrn: entity
        """

        new_entity = self.__model__(**kwargs)
        self.db.add(new_entity)
        return new_entity

    def bulk_add(self, entities: List):
        """Bulk adds the list of entities to the db and return the new new objects.
        Entities are given as dicts, with the key value pairs needed to create the entity

        :param entities: list of dicts
        :type entities: list
        :return: list
        """

        result = self.db.scalars(
            insert(self.__model__).returning(self.__model__), entities
        )
        return result.all()

    def update(self, entity, **kwargs):
        """Updates existing entity in db but does not commit

        :param entity: entity to update in the db
        :type entity: __model__
        :param kwargs: kwargs to update the model with
        :type kwargs: dict
        """

        for key, value in kwargs.items():
            setattr(entity, key, value)
        self.db.add(entity)

    def bulk_update(self, entities: List):
        """Bulkd updates the given list of entites. List of dicts which must contain the primary
        key so that the entity can be found to update

        :param entities: list of dicts which contain the fields to udpate on models
        :type entities: list
        """

        self.db.execute(update(self.__model__), entities)

    def delete(self, entity):
        """Delets the specified entity from the db

        :param entity: eneity to remove from the database
        :type entity: __model__
        """

        self.db.delete(entity)
