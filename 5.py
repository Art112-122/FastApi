import os
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field, field_validator, EmailStr, FutureDatetime
import datetime
import aiomysql
import uvicorn
from dotenv import load_dotenv
from fastapi.responses import JSONResponse
from fastapi import FastAPI, HTTPException, status, Query

load_dotenv()

headers = {
    'Accept': 'application/json'
}


class Book(BaseModel):
    """Base book model"""
    title: str = Field(max_length=50)
    author: str = Field(max_length=50)
    description: str = Field(max_length=200)
    count: int
    year: datetime.date


class Event(BaseModel):
    """Base event model"""
    title: str = Field(max_length=50)
    user: str = Field(max_length=50)
    description: str = Field(max_length=200)
    time: FutureDatetime = Field(...)


class EventEdit(BaseModel):
    """Event model for edit"""
    title: str = Field(max_length=50)
    user: str = Field(max_length=50)
    description: str = Field(max_length=200)


class User(BaseModel):
    """Base user model"""
    name: str = Field(..., max_length=50, min_length=2)
    surname: str = Field(..., max_length=50, min_length=2)
    email: EmailStr = Field(...)
    password: str = Field(..., max_length=20, min_length=5)
    phone: str = Field(..., pattern=r"\+?\d{10,15}")
    is_admin: bool = Field(default=False)

    @classmethod
    @field_validator('name', 'surname', mode='before')
    def check_letters(cls, value: str) -> str:
        if any(char.isdigit() for char in value):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Fields 'name' and 'surname' must not contain numbers")
        return value

    @classmethod
    @field_validator('password', mode='before')
    def validate_password(cls, v: str) -> str:
        if not any(c.islower() for c in v):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Password must contain at least one lowercase letter")
        if not any(c.isupper() for c in v):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must contain at least one digit")
        if not any(c in "!@#$%^&*()_-+=[]{}|\\:;\"'<>,.?/~`" for c in v):
            HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                          detail="Password must contain at least one special character")
        return v


MYSQL_CONNECTION_DATA = {
    "host": os.environ.get("MYSQL_HOST"),
    "port": int(os.environ.get("MYSQL_PORT", 3306)),
    "user": os.environ.get("MYSQL_USER"),
    "password": os.environ.get("MYSQL_PASSWORD"),
    "db": os.environ.get("MYSQL_DB"),
}


async def get_mysql_connection() -> aiomysql.Connection:
    return await aiomysql.connect(**MYSQL_CONNECTION_DATA)


@asynccontextmanager
async def create_tables(_: FastAPI):
    """
    Створення таблиць в БД при старті програми та закриття з'єднання з БД після завершення.
    """
    async with aiomysql.connect(**MYSQL_CONNECTION_DATA) as connection:
        cursor: aiomysql.Cursor = await connection.cursor()
        await cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS event (
                id INT AUTO_INCREMENT,
                title VARCHAR(50) NOT NULL,
                user VARCHAR(50) NOT NULL,
                description VARCHAR(200) NOT NULL,
                members TEXT,
                time DATETIME,
                PRIMARY KEY(id)
            );
            """
        )
        await cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT,
                name VARCHAR(50) NOT NULL UNIQUE,
                surname VARCHAR(50) NOT NULL,
                email VARCHAR(50) NOT NULL,
                password VARCHAR(20) NOT NULL,
                phone TEXT,
                isadmin BOOLEAN,
                PRIMARY KEY(id)
            );
            """
        )
        await cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS books (
                id INT AUTO_INCREMENT,
                title VARCHAR(50) NOT NULL,
                author VARCHAR(50) NOT NULL,
                description VARCHAR(200),
                count INTEGER,
                year DATE,
                PRIMARY KEY(id)
            );
            """
        )
        await connection.commit()

    yield


app = FastAPI(title="Books api", lifespan=create_tables)


@app.post("/books/add/")
async def create_book(book: Book):
    try:
        connection = await get_mysql_connection()
        cursor: aiomysql.Cursor = await connection.cursor()
        await cursor.execute(
            """
            INSERT INTO books (title, author, description, year, count)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (book.title, book.author, book.description, book.year, book.count)
        )
        await connection.commit()
        return JSONResponse("Book has been added.", status_code=status.HTTP_201_CREATED)
    except aiomysql.Error as e:
        raise e


@app.post("/events/add/")
async def create_event(event: Event):
    try:
        connection = await get_mysql_connection()
        cursor: aiomysql.Cursor = await connection.cursor()
        await cursor.execute(
            """
            SELECT isadmin FROM users WHERE name = %s;
            """,
            (event.user,)
        )
        resp = await cursor.fetchone()
        if isinstance(resp, tuple):
            if resp[0]:
                await cursor.execute(
                    """
                    INSERT INTO event (title, user, description, time, members)
                    VALUES (%s, %s, %s, %s, '')
                    """,
                    (event.title, event.user, event.description, event.time)
                )
            else:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                    detail='User hasn`t permissions for create event')
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
        await connection.commit()
        return JSONResponse("Event has been added.", status_code=status.HTTP_201_CREATED)
    except aiomysql.Error as e:
        raise e


@app.post("/users/add/")
async def create_user(user: User):
    try:
        user.check_letters(user.name)
        user.check_letters(user.surname)
        user.validate_password(user.password)
        connection = await get_mysql_connection()
        cursor: aiomysql.Cursor = await connection.cursor()
        await cursor.execute(
            """
            INSERT INTO users (name, surname, email, phone, password, isadmin)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (user.name, user.surname, user.email, user.phone, user.password, user.is_admin)
        )
        await connection.commit()
        return JSONResponse("User has been added.", status_code=status.HTTP_201_CREATED)
    except aiomysql.IntegrityError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already exists")
    except aiomysql.Error as e:
        raise e


@app.get("/books/get")
async def get_all_books():
    try:
        connection = await get_mysql_connection()
        cursor = await connection.cursor(aiomysql.DictCursor)
        await cursor.execute("SELECT * FROM books;")
        resp = await cursor.fetchall()
        return resp
    except aiomysql.Error as e:
        raise e


@app.get("/event/get")
async def get_all_events():
    try:
        connection = await get_mysql_connection()
        cursor = await connection.cursor(aiomysql.DictCursor)
        await cursor.execute("SELECT * FROM event;")
        resp = await cursor.fetchall()
        return resp
    except aiomysql.Error as e:
        raise e


@app.get("/books/get/{book_id}")
async def get_for_id_book(book_id):
    try:
        connection = await get_mysql_connection()
        cursor = await connection.cursor(aiomysql.DictCursor)
        await cursor.execute("SELECT * FROM books WHERE id = %s;", (book_id,))
        resp = await cursor.fetchall()
        return resp
    except aiomysql.Error as e:
        raise e


@app.get("/event/get/{event_id}")
async def get_for_id_event(event_id):
    try:
        connection = await get_mysql_connection()
        cursor = await connection.cursor(aiomysql.DictCursor)
        await cursor.execute("SELECT * FROM event WHERE id = %s;", (event_id,))
        resp = await cursor.fetchall()
        return resp
    except aiomysql.Error as e:
        raise e


@app.put("/event/update/{id}")
async def update_event(id: int, event: EventEdit):
    try:
        connection = await get_mysql_connection()
        cursor: aiomysql.Cursor = await connection.cursor()
        await cursor.execute(
            """
            SELECT isadmin FROM users WHERE name = %s;
            """,
            (event.user,)
        )
        resp = await cursor.fetchone()
        if isinstance(resp, tuple):
            if resp[0]:
                await cursor.execute("""
                            SELECT id FROM event WHERE id = %s;
                        """, (id,))
                check = await cursor.fetchone()
                if isinstance(check, tuple):
                    await cursor.execute(
                        """UPDATE event
                              SET title = %s, description = %s
                              WHERE id = %s;""",
                        (event.title, event.description, id))
                    await connection.commit()
                    return JSONResponse("Event has been updated.", status_code=status.HTTP_200_OK)
                else:
                    raise HTTPException(status_code=404, detail="Event not found")
            else:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                    detail='User hasn`t permissions for edit event')
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    except aiomysql.Error as e:
        raise e


@app.patch("/event/update/{id}/reschedule")
async def update_date(id: int, user: str = Query(...), datetime_: FutureDatetime = Query(...)):
    try:
        connection = await get_mysql_connection()
        cursor: aiomysql.Cursor = await connection.cursor()
        await cursor.execute(
            """
            SELECT isadmin FROM users WHERE name = %s;
            """,
            (user,)
        )
        resp = await cursor.fetchone()
        if isinstance(resp, tuple):
            if resp[0]:
                await cursor.execute("""
                            SELECT id FROM event WHERE id = %s;
                        """, (id,))
                check = await cursor.fetchone()
                if isinstance(check, tuple):
                    await cursor.execute(
                        """UPDATE event
                              SET time = %s
                              WHERE id = %s;""",
                        (datetime_, id))
                    await connection.commit()
                    return JSONResponse("Event has been updated.", status_code=status.HTTP_200_OK)
                else:
                    raise HTTPException(status_code=404, detail="Event not found")
            else:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                    detail='User hasn`t permissions for edit event')
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    except aiomysql.Error as e:
        raise e


@app.patch("/event/update/{id}/rsvp")
async def update_members(id: int, user: str = Query(...), member_id: int = Query(...)):
    try:
        connection = await get_mysql_connection()
        cursor: aiomysql.Cursor = await connection.cursor()
        await cursor.execute(
            """
            SELECT isadmin FROM users WHERE name = %s;
            """,
            (user,)
        )
        resp = await cursor.fetchone()
        if isinstance(resp, tuple):
            if resp[0]:
                await cursor.execute("""
                            SELECT members FROM event WHERE id = %s;
                        """, (id,))
                check = await cursor.fetchone()
                print(check)
                if isinstance(check, tuple):
                    await cursor.execute(
                        """
                        SELECT id FROM users WHERE id = %s;
                        """,
                        (member_id,)
                    )
                    answer = await cursor.fetchone()
                    if isinstance(answer, tuple):
                        list_ = f"{check[0]}".split(",")
                        if answer[0] not in list_:

                            await cursor.execute(
                                """UPDATE event
                                      SET members = %s
                                      WHERE id = %s;""",
                                (f"{check[0]}{member_id},", id))
                            await connection.commit()
                            return JSONResponse("Member has been added.", status_code=status.HTTP_201_CREATED)
                        else:
                            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Member already registed")
                    else:
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
                else:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
            else:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                    detail='User hasn`t permissions for edit event')
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    except aiomysql.Error as e:
        raise e


@app.delete("/event/delete/{event_id}")
async def delete_event(event_id: int, user: str = Query(...)):
    try:

        connection = await get_mysql_connection()
        cursor: aiomysql.Cursor = await connection.cursor()
        await cursor.execute(
            """
            SELECT isadmin FROM users WHERE name = %s;
            """,
            (user,)
        )
        resp = await cursor.fetchall()
        if isinstance(resp, tuple):
            if resp[0]:
                await cursor.execute("""
                            SELECT id FROM event WHERE id = %s;
                        """, (event_id,))
                check = await cursor.fetchone()
                if isinstance(check, tuple):
                    await cursor.execute(
                        """
                            DELETE FROM event WHERE id = %s;
                            """,
                        (event_id,))
                    await connection.commit()
                    return JSONResponse("Event has been deleted.", status_code=status.HTTP_201_CREATED)
                else:
                    raise HTTPException(status_code=404, detail="Task not found")
            else:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                    detail='User hasn`t permissions for create event')
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    except aiomysql.Error as e:
        raise e


if __name__ == "__main__":
    filename = os.path.basename(__file__).split('.')[0]
    uvicorn.run(f"{filename}:app")
