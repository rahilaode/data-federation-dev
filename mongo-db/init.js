db = db.getSiblingDB('people_db');

db.createCollection('person');

db.person.insertMany([
  { first_name: "Rahil", last_name: "Atansa" },
  { first_name: "Jane", last_name: "Smith" },
  { first_name: "Michael", last_name: "Johnson" },
  { first_name: "Emily", last_name: "Brown" },
  { first_name: "David", last_name: "Wilson" }
]);
